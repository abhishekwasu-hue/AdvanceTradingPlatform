# Production host - Hostinger KVM 2 (runbook)

> Written in Marathi first (`DEPLOY_HOSTINGER_MR.md`); translated to English in Oct 2026 - the owner's ruling: ATP is
> English only, docs included. The steps are unchanged.

> **Z11 (2026-10-11):** the one owner command is now `deploy/atp.sh` (install / deploy / health / rollback / backup /
> restore-check) - English runbook `docs/deploy/HOSTINGER_RUNBOOK.md`. Blocks 2 and 3 below (`deploy.sh`, `rollback.sh`) are
> superseded by it: their one-liners are removed and they refuse to run once `atp.sh` has recorded a release. Block 1
> (bootstrap) stays the first step; the second paste is now `install.sh`.

Host: **Hostinger KVM 2** - 2 vCPU / 8 GB RAM / 100 GB NVMe, Ubuntu 24.04, Mumbai, static IPv4. This is ATP's production
host (starting with PAPER). **The Trade repo's DigitalOcean droplet stays separate - nothing in this runbook applies to
it, do not touch it.**

Three blocks, each **one line** (`bash -c "$(echo <base64> | base64 -d)"`) - the lines are in
`docs/DEPLOY_HOSTINGER_ONELINERS.txt` (built from the scripts in the repo; a CI test checks they match those scripts).
To read them, the source scripts: `deploy/hostinger/bootstrap.sh`, `deploy.sh`, `rollback.sh`, `status.sh`.
To rebuild the lines on the PC: `bash deploy/hostinger/oneliners.sh`.

> **Secrets:** `.env` only on the server (`/opt/atp/.env`, mode 600). Never in a chat, GitHub or a PR. Broker/AI keys
> only through the Settings page (encrypted). `SECRETS_ENCRYPTION_KEY` = the same 44-character key as on the PAPER PC -
> **never a new one**.

## 0. First (once, you)

| # | What | Where |
|---|---|---|
| 0.1 | Write down the server IP (static IPv4) | Hostinger hPanel → VPS → Overview |
| 0.2 | Add your SSH public key to the VPS (no password login) | hPanel → VPS → Settings → SSH keys |
| 0.3 | With a domain: an `A` record `atp.<domain>` → IP | the registrar; without one, use the IP for now (below) |
| 0.4 | Hostinger's VPS firewall (if any): 22/80/443 open | hPanel → VPS → Firewall |
| 0.5 | Cloudflare R2 bucket `atp-backups` + a bucket-scoped token (off-site backups) | Cloudflare → R2 (§5) |

## 1. Block 1 - bootstrap (root, once)

```
ssh root@<server-ip>
# paste the "bootstrap.sh" line from docs/DEPLOY_HOSTINGER_ONELINERS.txt
```

It does: user `atp` + sudo (root's SSH key), SSH key-only (password off, root login off), ufw (22/80/443 only),
fail2ban (sshd), unattended security upgrades (auto-reboot **off** - no reboot during market hours), Docker + compose,
2 GB swap, timezone Asia/Kolkata, chrony. Running it again breaks nothing.

**At the end:** check in a new terminal that `ssh atp@<server-ip>` works - **only then** close the root window.

## 2. Block 2 - deploy (as the `atp` user; the first time and on every update)

```
ssh atp@<server-ip>
# paste the "deploy.sh" line
```

It stops in two places (exit 10) - do the work and run the same line again:

1. **Deploy key (read-only):** the printed `ssh-ed25519 ...` key → GitHub → AdvanceTradingPlatform → Settings → Deploy
   keys → Add (**Allow write access off**).
2. **`.env`:** `/opt/atp/.env` is created (from `.env.example`, `CHANGE_ME` placeholders, mode 600). Fill it in with
   `nano /opt/atp/.env`:
   - `POSTGRES_PASSWORD`, `JWT_SECRET_KEY`, `METRICS_TOKEN`, `BACKUP_ENCRYPTION_PASSPHRASE`:
     `python3 -c "import secrets;print(secrets.token_urlsafe(48))"`
   - `SECRETS_ENCRYPTION_KEY`: the same key as on the PAPER PC (do not change it).
   - With a domain: `DOMAIN=atp.<domain>`, `ACME_EMAIL=<your email>`, `CADDYFILE=Caddyfile` (Let's Encrypt).
   - Without a domain: `DOMAIN=<server-ip>`, `ACME_EMAIL=ops@localhost`, `CADDYFILE=Caddyfile.ip` (Caddy's internal
     certificate - the browser warns once; plain HTTP does not work because the refresh cookie is Secure).

Then: build → migration guard (refuses schema changes while the market is open; run after 15:30) → `alembic upgrade head` →
API → worker → frontend → Caddy/backup/offsite → health → `first_paper_day_check.py` (read-only).
For an update, the same line (or `bash /opt/atp/deploy/hostinger/deploy.sh`); another ref: `ATP_REF=<sha|tag> bash ...`.

Expected: `deployed <sha>`; `curl -sk https://<DOMAIN>/api/system/health` → `{"status":"ok",...}`.

## 3. Block 3 - rollback

```
bash /opt/atp/deploy/hostinger/rollback.sh            # the previous deployed commit (.deploy/history)
bash /opt/atp/deploy/hostinger/rollback.sh <sha>      # a given commit
```

The same rolling path as a deploy (the API first, the worker once it is healthy). The schema does not go back (every
migration is additive; the old image reads the new schema). Preferably while the market is closed.

## 4. "What is running" - status

```
bash /opt/atp/deploy/hostinger/status.sh
```

commit, containers, each one's memory, host RAM/disk/swap, API/deep health, worker heartbeat, migrations, the last
backup, the off-site copy, public ports (only 22/80/443 expected), ufw, the certificate, and the **egress IP** (for broker
whitelisting - §6).

## 5. Backups (H-2: Cloudflare R2)

> **Superseded (A1, 23 Oct 2026):** the current steps are `docs/deploy/HOSTINGER_RUNBOOK.md` sections 6, 6a, 6b, 6c
> and 11. On Hostinger the WAL archive is now off and never copied to R2; R2 is pruned by `atp.sh` by count, so the
> 35-day lifecycle rule below must **not** be added (it would delete the monthly copies). This section is kept for
> the history of the earlier blocks.

| What | How | Where |
|---|---|---|
| Daily Postgres dump (encrypted, SHA-256) | compose `backup` service (24 h), retention 14 days | volume `backups` |
| WAL (PITR, ≤ 5 minutes RPO) | Postgres `archive_command` | volume `wal_archive` |
| Off-site copy (hourly) | compose `offsite` (rclone): `copy` dumps, `sync` WAL | **Cloudflare R2** bucket `atp-backups` (`OFFSITE_REMOTE=offsite:atp-backups`) |

**Create R2 once (you, in the Cloudflare dashboard; cost: 10 GB + egress free, within the $5 limit):**

1. Cloudflare → R2 → *Create bucket* `atp-backups` (location hint: Asia-Pacific). Public access **off**.
2. R2 → *Manage API tokens* → *Create API token*: permission **Object Read & Write**, bucket `atp-backups` only,
   no TTL. (No Admin token - the token must not be able to delete the bucket.)
3. The three values you get: Access Key ID, Secret Access Key, endpoint `https://<account id>.r2.cloudflarestorage.com` →
   **only on the server** `nano /opt/atp/.env`: `OFFSITE_S3_ENDPOINT`, `OFFSITE_S3_ACCESS_KEY`, `OFFSITE_S3_SECRET_KEY`
   (the deploy block stops at `CHANGE_ME` until then). Never in a chat/GitHub.
4. R2 → bucket → *Settings → Object lifecycle rules*: prefix `backups/`, delete after 35 days (the off-site copy never
   deletes - this rule keeps it within the free 10 GB). No rule for `wal_archive/` (the sync prunes it itself).
5. Run block 2 again. `status.sh` must show, under "off-site", `remote: offsite:atp-backups`, `synced`, the last dumps
   and the total size.

Alternatives (provider seam): Backblaze B2 - `OFFSITE_REMOTE=b2:<bucket>`, `OFFSITE_B2_KEY_ID`, `OFFSITE_B2_APPLICATION_KEY`
(a bucket-scoped application key). Another S3-compatible store: `OFFSITE_S3_PROVIDER` + `OFFSITE_S3_ENDPOINT`. A box with
no provider: `OFFSITE_REMOTE=/offsite_local` (the host disk - not a real off-site copy).

**Restore test - from R2 (once a week, a scratch database - production is not touched):**

```
cd /opt/atp && C="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hostinger.yml -p atp"
F="$($C exec -T offsite sh /scripts/offsite_fetch.sh /restore)" && $C run --rm --entrypoint sh backup /scripts/verify_backup.sh "$F"
```

The first line brings the newest dump on R2 + its `.sha256` into `/var/backups/atp-restore-test`; the second checks that
sha256, restores it into a scratch database, verifies it and drops the database. Expected: one line of JSON,
`"status": "verified"`. From the local copy (without R2): `$C run --rm --entrypoint sh backup /scripts/verify_backup.sh latest`.
PITR drill: `docs/OPERATIONS.md` (Phase O5).

## 6. SEBI - static IP registration (each broker)

SEBI retail-algo framework: API orders only from a static IP registered with the broker. The server's IP = the "egress
IP" in `status.sh` (it should equal the IPv4 in hPanel).

| Broker | Where to register it | Note |
|---|---|---|
| Upstox | developer.upstox.com → My Apps → app → Static IP | ATP's own app (not the Trade bots') |
| Zerodha (Kite Connect) | developers.kite.trade → app → IP whitelist | |
| Fyers | myapi.fyers.in → app → whitelisted IPs | the PAPER PC's IP is different - whether to register both depends on the broker's rules |
| Dhan / Angel One / Shoonya | "static IP" in that broker's API console | |

Check (each broker): IP registered ☐ · date of the change noted ☐ · backup IP (if any) ☐ · Settings → Brokers →
Read-only check 9/9 ☐. **To change the IP** (a Hostinger IP change / a new server): first register the new IP with every
broker, only then switch - see each broker's page for its change rules (how often per week). A broker may refuse a shared
IP (one IP for two clients) - ATP's and the Trade droplet's IPs are different. (Per-tenant IP model + Settings UI = part D4.)

## 7. How everything fits in 8 GB

`docs/OPERATIONS.md` → "Resource budget - 8 GB host". In short: Postgres 2 GB, API 1.5 GB, worker 1.5 GB, Redis
0.5 GB, backup 0.5 GB, Caddy/frontend/offsite ≈ 0.6 GB → ≈ 6.6 GB; the remaining ≈ 1.4 GB for the OS + page cache;
the 2 GB swap is only a safety margin. If the memory column of `status.sh` shows a service near its limit, change `MEM_*`
in `.env` and run block 2.

## 8. When something goes wrong

| You see | Do |
|---|---|
| `ssh atp@...` does not work | do not close the root window; `cat /home/atp/.ssh/authorized_keys`; hPanel → Browser terminal |
| deploy: migration guard refused | the market is open - again after 15:30 IST |
| `public https://... -> 000` | DNS has not propagated yet / `CADDYFILE` and `DOMAIN` do not match; `docker compose ... logs caddy` |
| container `OOMKilled` | `status.sh` memory; raise that service's `MEM_*` in `.env` (total ≤ ~7 GB) |
| first_paper_day_check ❌ | the table in `docs/GO_LIVE_RUNBOOK.md` §3 |
