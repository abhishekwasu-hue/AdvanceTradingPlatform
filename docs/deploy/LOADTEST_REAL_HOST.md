# Load test on the production host, in market hours (`atp.sh loadtest`)

The owner's run of the 500-session load test against the real Hostinger KVM 2 (2 vCPU, 8 GB), during NSE market
hours, with memory measured for LAB-6. It complements the CI staging-stack run (`docs/reports/load_test_500.md`) and
the copy-of-the-stack run (`deploy/ci/loadtest_500.sh`, HOSTINGER_RUNBOOK section 12a): this one measures the
production stack itself - its API, worker, Postgres, Redis and Caddy as they run, with the market open.

## The command block

Paste this as `atp` on the server (`ssh atp@187.126.125.208`). It takes about 13 minutes.

```
# DEPLOY NOTE: the latest release must already be deployed OUTSIDE market hours (before 09:15 or after 15:30 IST),
# never during the session:   /opt/atp/deploy/atp.sh deploy
# Then, on a trading day from 21 Oct, start between 09:20 and 15:15 IST:
/opt/atp/deploy/atp.sh health || { echo "health check failed - load test not started"; false; } && { /opt/atp/deploy/atp.sh loadtest; echo "loadtest exit code: $?"; }
```

At its end, `loadtest` prints the report of this run between `===== report ... (paste this back) =====` and
`===== end of report =====`. Paste that part back. It holds no secret, no password and no personal data.
- If `health` fails, `loadtest` does not start: the block prints "health check failed - load test not started" and no
  "loadtest exit code" line. The exit codes below are the one after "loadtest exit code:".
- A refusal (exit 3, 6 or 10, or exit 1 from a preflight check before seeding: a service down, deep health not ok,
  the trading worker not ok) creates nothing and prints no report.

Exit codes, in this order of precedence:

| Code | Meaning |
|---|---|
| `4` | the cleanup was **not verified** (the synthetic organisation, or the run's network still attached to Caddy), whatever else happened (see "Manual cleanup") |
| `5` | the watchdog stopped the run because production degraded (the reason is in the report); cleanup verified |
| `130` | stopped with Ctrl-C; cleanup verified |
| `1` | an SLO missed, or the run failed; cleanup verified |
| `0` | every SLO met, cleanup verified |
| `3` | refused: outside NSE market hours, or too little time left before 15:30; nothing created |
| `6` | refused: not enough free memory for the client and the watchdog's floor; nothing created |
| `10` | refused: not a PAPER-only Phase 0 deployment, or nothing deployed; nothing created |
| `2` | wrong usage |

## What it does

1. **Preflight.**
   - It runs as `atp`, not root, and takes the same lock as `deploy`, so the two never overlap.
   - It reads `.env` key names only.
   - It needs a deployed release and its `atp-backend` image, every service up, and deep health ok.
   - It needs the trading worker as the watchdog does (step 5): status `ok` (not `never_seen`, `stale` or
     `stale_market_open`) and its heartbeat within the limit. Otherwise it refuses with exit 1 before anything is
     created.
   - It needs enough free memory: about 48 MiB per client container (25 for 500 users; one measured for 20 sessions:
     about 31 MiB), plus the watchdog's floor (400 MiB) and 256 MiB to spare. That is about 1.9 GB of MemAvailable
     for 500 users. Less is refused, with exit 6.
2. **Market-hours guard.**
   - NSE must be open by the app's own calendar (`backend/scripts/session_check.py`, the same reading `deploy` uses:
     weekends and exchange holidays included).
   - When the calendar does not answer, it falls back to the clock (Monday-Friday 09:15-15:30 IST) and says that
     holidays are unknown.
   - It also refuses when fewer minutes are left before 15:30 than the run needs.
   - `--force-offhours` runs anyway. The report then says, at the top, that the run is not the market-hours result.
3. **PAPER-only guard.**
   - It reads the posture the way `atp.sh health` does: `backend/scripts/flag_posture.py` in the running API, and
     `ATP_DEPLOYMENT_PHASE` in `.env`.
   - It refuses with exit 10 unless all of these hold: phase0, `live_trading` off for everyone and for every
     organisation, and `phase0_paper_only` on.
   - It never changes a flag. The seeding checks this again on its own.
4. **Synthetic users.** One organisation, `loadtest-<YYYYMMDD-HHMMSS>`, with 500 users `lt-<stamp>-0000@example.com`
   ... (`backend/scripts/loadtest_org.py`, the production twin of the CI's `deploy/ci/seed_users.py`).
   - It is made in **one transaction**: the organisation, its data key (as a sign-up gets), the hash-chained audit row
     `loadtest_org_seeded`, and every user. It exists whole, or not at all.
   - One random password, hashed once. It is kept only in `.deploy/loadtest/<stamp>/accounts.csv` (mode 600) and
     deleted at cleanup.
   - The seed reports the organisation id and every user id. They are kept in `.deploy/loadtest/<stamp>/org.json`.
   - Email marked verified. The first user is the owner; the rest are traders. PAPER only.
   - No broker credentials and no broker session. The run checks that the organisation has 0 broker credentials, by
     its id.
   - The seed runs in a named container. Ctrl-C during seeding stops that container (its transaction rolls back)
     before the cleanup.
5. **Samplers and the watchdog.**
   - Every 5 s (`--sample-seconds`), until the load ends:
     - `docker stats --no-stream`: every container's CPU and memory, the client's containers included;
     - `/proc/meminfo` (MemTotal, MemFree, MemAvailable, SwapTotal, SwapFree);
     - `/proc/vmstat` (pages swapped in and out).
   - The API's `/metrics` are read before and after.
   - **Every 30 s the watchdog checks production.** It stops the run (exit 5, cleanup still runs) at the first of:
     - deep health failing twice in a row;
     - the trading worker's heartbeat older than 3 worker cycles (3 x the `cycle_seconds` deep health reports, never
       less than 180 s nor more than 900 s), or the worker not ok;
     - MemAvailable under 400 MiB;
     - swap use grown by more than 512 MiB since the start;
     - any app container restarted or OOM-killed.
6. **The load.** It uses the existing session model (`backend/scripts/loadtest.py`):
   - each user logs in on a 150 s ramp, then for 300 s polls the dashboard (5 reads), and with some probability
     opens proposals, PAPER positions and account, a chart, a shared-lake chart, and a screener preview;
   - it waits 30 s on average between loops, as the Dashboard page refreshes;
   - it never places, changes or approves anything.
7. **Cleanup** (always - see below). Then the **summary** is printed and written to
   `/opt/atp/reports/loadtest-<stamp>.md` (`loadtest-latest.md` points to the newest report), and the report is
   printed. Raw samples and client logs stay in `/opt/atp/.deploy/loadtest/<stamp>/`.

Options: `--users N` (default 500), `--ramp S` (150), `--seconds S` (300 after the ramp), `--think S` (30),
`--client-cpus C` (0.25 per client container), `--sample-seconds S` (5), `--force-offhours`, and `--check` (or
`--dry-run`). `--check` runs every guard, then stops. It creates and changes nothing. Ramp + duration stay within 14
minutes: access tokens live 15 minutes and the client does not refresh them.

## Where the load generator runs, and why

The client runs **on this host**, in small containers, each with its own IP, on a network of the run's own:
- The network is `docker network create --internal atp-loadtest-<stamp>`: it has no route out.
- Caddy is attached to it for the run, and detached and the network removed afterwards. Nothing else is on it.
- So the clients reach only the public edge. They cannot reach Postgres, Redis or `backend:8000`.
- With a domain (`CADDYFILE=Caddyfile`), the site name resolves to Caddy's address on that network, and the public
  certificate is verified.
- With the IP edge (`Caddyfile.ip`), the client sends the site name as `Host`, and checks Caddy's internal root
  certificate (the chain, not the name).

Why there, and not from another machine against the public URL:
- **The login limit.** The API allows 10 logins a minute per client IP. One remote machine is one IP: 500 logins would
  take over 50 minutes, or the limit would have to be bypassed. LD-9's login p95 at 500 sessions could not be measured.
  On the host, Caddy forwards each container's own IP to the API, so the limit applies per container as for real
  users: 25 containers, at most 8 logins a minute each. Nothing is bypassed.
- **Latency.** A home connection would add its own latency and jitter to every p95, against a 0.5 s budget that is the
  server's.
- **Simplicity.** No accounts file leaves the server.

The cost is CPU and memory on the 2-vCPU box. It is bounded and shown in the report:
- Each client container is capped at `--cpus 0.25`, and has 128 MiB memory with no swap. Each one shares a single TLS
  context between its sessions.
- **CPU weight.** Each client container gets `--cpu-shares` 1024 / 25 = 40. On cgroup v2, docker maps that to a
  weight of about 2, against about 39 for each app container at the default 1024. When the CPU is contended, the 25
  client containers together weigh about 50, a bit more than one app container. The app's containers together (API,
  worker, Postgres, Redis, Caddy, frontend) weigh far more, so they get the CPU first.
- They run read-only, without capabilities, as `atp`'s uid, with no secret in their environment.
- Their CPU (mean and peak, all containers summed, as a share of one vCPU and of the box) and memory are reported
  apart from the app's.
- The report also counts the samples where a client container was at 90% or more of its cap. Many of these mean the
  client itself may have added latency: rerun with `--client-cpus 0.5`.

The trade-off cuts both ways: on a saturated box the client is starved first, so its measured latency includes some of
its own waiting.

## How the charts get data in the load-test organisation

The organisation has no broker session, by design: no request of its users can reach a broker or spend Abhi's broker
limits.
- **`chart`** (`POST /api/market-data/candles`) reads candles through the user's own broker session. Here it answers
  409 "no broker session" before any broker call. The report lists it as **not measured**, never as passed. LD-10
  (merged in #282) shares fetched candles between users of the same broker, but each fetch still needs the requesting
  user's own session, so this stays 409 here. This tool does not depend on LD-10's code; if a later change serves
  charts without a session of one's own, the same scenario is measured with no change here.
- **`chart_lake`** (`GET /api/market-data/history`, a year of daily bars of the same 10 NSE symbols) is added to the
  session model for this run. It is the chart data such an organisation can see: the shared market lake, a database
  read with no broker. It is judged against 0.5 s like any other API scenario. An answer without bars (the lake holds
  nothing for that symbol) is counted apart in the outcomes table, so an empty lake shows.
- **`screener_run`** is like `chart`: 409 here, reported as not measured.

## The verdict

These are the owner's decisions: LD-9 for login, SLO-1 for the rest.
- **Every API scenario** (dashboard reads, proposals, positions, PAPER account, `chart_lake`, and `chart` /
  `screener_run` when measured): p95 at most **500 ms**.
- **Login**, measured apart: p95 at most **2,000 ms** at 500 concurrent sessions, with every user logged in.
- **Zero errors**: server errors, transport failures (time-outs included), other 4xx answers, and rate-limit refusals.
  Answers the deployment gives on purpose are reported, not judged: 409 "no broker session", and a feature switched off.

The report has:
- the setup, the watchdog's reason if it stopped the run, the SLO lines in plain English, and p50 / p95 / p99 per
  scenario;
- outcomes per scenario, and the after-the-ramp split (diagnostic);
- memory per container (mean, peak, limit, peak % of the limit, CPU);
- the host's memory: lowest MemAvailable, peak used, lowest MemFree, swap used at the start and at the peak, and
  pages swapped in and out;
- the load client's CPU and memory, and the server side: time per route, pool and bcrypt waits;
- the cleanup result, and the safety checks: flags unchanged, the other organisations unchanged, no broker credentials.

## Safety

- PAPER only, never LIVE. It never places an order, never calls a broker, never blocks an exit, and the AI places
  nothing.
- The load is read-only apart from what a login writes: a session, a login event, an audit row, and a new-device
  notification. A PAPER account card also creates the user's PAPER account. All of these are inside the load-test
  organisation.
- It never changes another organisation, a feature flag, `.env`, a secret, `SECRETS_ENCRYPTION_KEY`, or a Postgres
  role or password.
- Two read-only digests check this. The feature-flag row and every other organisation with its users (ids, names,
  status, plan, emails, roles, active) are compared before and after. A real sign-up or login during the run can change
  the second digest; the report then says "not confirmed", not "changed".
- It refuses outside market hours (unless `--force-offhours`), and on anything but a PAPER-only Phase 0 deployment.
- It runs as `atp`, never root, and holds the deploy lock.
- The only change to a production container is the attachment of Caddy to the run's internal network. Caddy is
  detached when the run ends, and it is not restarted.

## Which organisation the cleanup may touch

A name and an email address are both things any user can choose: an owner can rename an organisation to
`loadtest-...`, and a sign-up can use an `lt-...@example.com` address. Neither ever makes an organisation a load-test
one. The cleanup acts on an organisation only when **all** of these hold:

1. There is exactly one `loadtest_org_seeded` audit row for it. Only the seed writes that event. The row must match its
   own hash, name the organisation's id and no user, and be written within 15 minutes of the stamp's time.
2. The organisation still has that id and that name, and was created in the same transaction as the row.
3. It has at least one member. An organisation with no member is never "ours".
4. Every member is one the seed made:
   - with `org.json` (the run's own cleanup, and the manual one when the file is still there): every member id must be
     in the list the seed reported;
   - without it: the address must be `lt-<stamp>-NNNN@example.com` (ASCII digits only) **and** the user must have been
     created in the seeding transaction.

`--list` and `--all` find organisations by their seed audit row, never by name. Each one is listed with whether it is
still provably a load-test one, and why not if it is not.

## Cleanup, and how to stop it

**Ctrl-C stops it.** So does `kill -INT <pid>` from another session, a closed SSH session (SIGHUP), or the watchdog.
Every exit runs the cleanup: the normal end, a failure, an abort, Ctrl-C, SIGTERM, or SIGHUP. While the cleanup runs,
Ctrl-C is ignored, and its docker commands run in their own session, so a second Ctrl-C cannot cut it short. After
SIGHUP the output goes to `.deploy/loadtest/<stamp>/run.log`. A `kill -9` cannot be trapped: use the manual cleanup.

Steps:
1. Stops the watchdog and the samplers, and a seed still running.
2. Removes every client container (label `atp.loadtest=<stamp>`) and checks that none is left. Detaches Caddy from the
   run's network and removes the network. A network still there (Caddy still attached) means the cleanup is not
   verified: exit 4.
3. Runs `backend/scripts/loadtest_org.py cleanup --stamp <stamp> --org-file - --verify-audit` with `org.json` on
   stdin, identifying the organisation as above. When the seed never reported back, it runs with `--absent-ok`: "no
   seed record at all for this stamp" then means nothing was created.
4. **Refuses, deleting nothing**, if the organisation or its users own rows in any table outside the allowlist below
   (a marketplace listing, a strategy, an order, ...). It names the tables. Look at them before cleaning by hand.
   Before refusing it still makes the (proven) organisation inert: every user deactivated with an unusable password,
   the organisation `suspended`, and one `loadtest_org_cleaned` audit row says so. The cleanup is reported not
   verified (exit 4).
5. Deletes rows only from the **allowlist** of tables a seed and the load's scenarios write. The list was measured: a
   seed, logins and every scenario against the test app wrote `tenant_keys`, `user_sessions`, `login_events`,
   `notifications`, `paper_accounts` and `audit_logs`. The list also has what those paths can add: `notification_reads`,
   `alert_deliveries`, `usage_records`, `email_verifications`, `password_resets`, `mfa_backup_codes` and
   `ai_acknowledgements`.
   - It deletes by `tenant_id`, or by `user_id` where a table has no organisation column.
   - It never deletes through a "who acted" column (`created_by`, `updated_by`, `engaged_by`, ...). Platform-wide rows
     and other organisations' rows are never deleted.
6. **Never deletes from the ledgers**: `audit_logs`, `audit_anchors`, `orders`, `order_events`, `trades`. This holds in
   the cleanup itself, whatever database role the app connects as. The audit log is a tamper-evident hash chain, and
   each login appended a row that names its user and the organisation. So:
   - A user any remaining row still names is kept as an **inert tombstone**: deactivated, with the password replaced by
     the hash of a random secret nobody holds. A login attempt gets an ordinary 401.
   - The other users are deleted. Nothing names them, so nothing can cascade.
   - The organisation is never deleted. It stays `suspended`, with the reason "loadtest <stamp>: synthetic
     organisation, cleaned up". One audit row `loadtest_org_cleaned` records the cleanup.
7. Counts again and prints:
   - what was deleted per table;
   - what was kept on purpose (the ledgers);
   - the tombstone: status, users, 0 active, 0 usable passwords;
   - what is left over (must be none);
   - the audit chain check, from its latest anchor.

   "cleanup verified" means nothing is left but the ledgers and the inert tombstones.
8. Deletes the accounts file.

Redis keys from the run (rate-limit windows, caches) expire by themselves. Access tokens issued in the run stop
working at once: the users are inactive and their sessions are deleted.

## Manual cleanup (if the cleanup ever fails)

As `atp`:

```
/opt/atp/deploy/atp.sh loadtest-cleanup --list          # every organisation a seed record names: provably ours or not, and why
/opt/atp/deploy/atp.sh loadtest-cleanup 20261021-101500  # one run (its stamp), idempotent
/opt/atp/deploy/atp.sh loadtest-cleanup --all            # every provable load-test organisation, client container and network
```

It uses `.deploy/loadtest/<stamp>/org.json` when the file is there. Otherwise it uses the organisation id from the
seed record. An organisation that is not provably a load-test one is listed and never touched. A load-test network
still there after it (Caddy still attached) is a cleanup not verified: exit 4.

If `atp.sh` itself cannot run:

```
# 1. the client containers and the run's network (Caddy detached first)
docker rm -f $(docker ps -aq --filter label=atp.loadtest)
for n in $(docker network ls -q --filter label=atp.loadtest); do docker network disconnect -f "$n" atp-caddy-1; docker network rm "$n"; done
# 2. the organisation, by the ids its seed reported (the running release's image; STAMP from `ls /opt/atp/.deploy/loadtest/`)
cd /opt/atp && ATP_RELEASE=$(cut -c1-12 .deploy/current) docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  -f docker-compose.hostinger.yml -f deploy/compose.release.yml -p atp run --rm --no-deps -T backend \
  python scripts/loadtest_org.py cleanup --stamp STAMP --org-file - --verify-audit < .deploy/loadtest/STAMP/org.json
# 3. the accounts file
rm -f /opt/atp/.deploy/loadtest/*/accounts.csv
```

If it refuses because of rows outside the allowlist, it names the tables. Its users are already deactivated and the
organisation suspended; nothing was deleted. Send that output: those rows are not what a load test writes and need a
look first. Never delete rows by hand: the audit chain must stay intact.
