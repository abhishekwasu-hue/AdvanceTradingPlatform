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
/opt/atp/deploy/atp.sh health && { /opt/atp/deploy/atp.sh loadtest; echo "loadtest exit code: $?"; cat /opt/atp/reports/loadtest-latest.md; }
```

The last command prints the report. Paste it back (it holds no secret, no password and no personal data).

Exit codes: `0` every SLO met and the cleanup verified; `1` an SLO missed, or the run failed; `3` refused: outside
NSE market hours, or too little time left before 15:30; `4` the SLOs were met but the cleanup was not verified (see
"Manual cleanup"); `10` refused: not a PAPER-only Phase 0 deployment, or nothing deployed; `130` stopped with Ctrl-C (it
still cleaned up).

## What it does

1. **Preflight.** It runs as `atp`, not root, and takes the same lock as `deploy`, so the two never overlap. It
   reads `.env` key names only. It needs a deployed release and its `atp-backend` image, every service up, deep
   health ok, and enough free memory for the client.
2. **Market-hours guard.** NSE must be open by the app's own calendar (`backend/scripts/session_check.py`, the
   same reading `deploy` uses: weekends and exchange holidays included). When the calendar does not answer, it
   falls back to the clock (Monday-Friday 09:15-15:30 IST) and says that holidays are unknown. It also refuses when
   fewer minutes are left before 15:30 than the run needs. `--force-offhours` runs anyway. The report then says, at
   the top, that the run is not the market-hours result.
3. **PAPER-only guard.** It reads the posture the way `atp.sh health` does (`backend/scripts/flag_posture.py` in the
   running API, and `ATP_DEPLOYMENT_PHASE` in `.env`). It refuses with exit 10 unless all of these hold: phase0,
   `live_trading` off for everyone and for every organisation, and `phase0_paper_only` on. It never changes a flag.
4. **Synthetic users.** It creates one organisation, `loadtest-<YYYYMMDD-HHMMSS>`, with 500 users
   `lt-<stamp>-0000@example.com` ... (`backend/scripts/loadtest_org.py`, the production twin of the CI's
   `deploy/ci/seed_users.py`):
   - One random password, hashed once. It is kept only in `.deploy/loadtest/<stamp>/accounts.csv` (mode 600) and
     deleted at cleanup.
   - Email marked verified. The first user is the owner; the rest are traders.
   - PAPER only, with no broker credentials and no broker session. The seeding refuses on its own too, unless the
     deployment is PAPER-only Phase 0.
   - Writes one audit row (`loadtest_org_seeded`), so the audit trail says the organisation is synthetic.
   - It checks afterwards that the organisation has 0 broker credentials.
5. **Samplers (LAB-6).** Every 5 s (`--sample-seconds`) until the end:
   - `docker stats --no-stream`: every container's CPU and memory, the client's containers included;
   - `/proc/meminfo` (MemTotal, MemFree, MemAvailable, SwapTotal, SwapFree);
   - `/proc/vmstat` (pages swapped in and out).

   The API's `/metrics` are read before and after, for its own time per route and its waits.
6. **The load.** It uses the existing session model (`backend/scripts/loadtest.py`):
   - each user logs in on a 150 s ramp, then for 300 s polls the dashboard (5 reads), and with some probability
     opens proposals, PAPER positions and account, a chart, a shared-lake chart, and a screener preview;
   - it waits 30 s on average between loops, as the Dashboard page refreshes;
   - it never places, changes or approves anything.
7. **Cleanup** (always - see below). Then the **summary** is printed and written to `/opt/atp/reports/loadtest-<stamp>.md`
   (`loadtest-latest.md` points to the newest report). Raw samples and client logs stay in `/opt/atp/.deploy/loadtest/<stamp>/`.

Options: `--users N` (default 500), `--ramp S` (150), `--seconds S` (300 after the ramp), `--think S` (30),
`--client-cpus C` (0.25 per client container), `--sample-seconds S` (5), `--force-offhours`, and `--check` (or
`--dry-run`). `--check` runs every guard, then stops. It creates and changes nothing. Ramp + duration stay within 14
minutes: access tokens live 15 minutes and the client does not refresh them.

## Where the load generator runs, and why

The client runs **on this host**, in small containers on the compose network (`atp_default`), and reaches Caddy over
HTTPS by its address there:
- With a domain (`CADDYFILE=Caddyfile`), the site name resolves to Caddy inside the client, and the public
  certificate is verified.
- With the IP edge (`Caddyfile.ip`), the client sends the site name as `Host`, and checks Caddy's internal root
  certificate (the chain, not the name).

Why there, and not from another machine against the public URL:
- **The login limit.** The API allows 10 logins a minute per client IP. One remote machine is one IP: 500 logins would
  take over 50 minutes, or the limit would have to be bypassed. LD-9's login p95 at 500 sessions could not be measured.
  On the host, each client container has its own IP on the compose network. Caddy forwards it to the API, so the limit
  applies per container as for real users: 25 containers, at most 8 logins a minute each. Nothing is bypassed.
- **Latency.** A home connection would add its own latency and jitter to every p95, against a 0.5 s budget that is the
  server's.
- **Simplicity.** No accounts file leaves the server.

The cost is CPU on the 2-vCPU box. It is bounded and shown in the report:
- Each client container is capped (`--cpus 0.25`), and has 192 MiB memory with no swap.
- All of them together weigh about as much as one app container when the CPU is contended (`--cpu-shares` 1024 / 25
  each), so the app gets the CPU first.
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
  409 "no broker session" before any broker call. The report lists it as **not measured**, never as passed. If LD-10
  (being built separately) lets a chart serve shared candles without a session of its own, the same scenario is
  measured with no change to this tool. This tool does not depend on LD-10.
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
- the setup, the SLO lines in plain English, and p50 / p95 / p99 per scenario;
- outcomes per scenario, and the after-the-ramp split (diagnostic);
- memory per container (mean, peak, limit, peak % of the limit, CPU);
- the host's memory: lowest MemAvailable, peak used, lowest MemFree, swap used at the start and at the peak, and
  pages swapped in and out;
- the load client's CPU and memory, and the server side: time per route, pool and bcrypt waits;
- the cleanup result, and the safety checks: flags unchanged, the other organisations unchanged, no broker credentials.

## Safety

- PAPER only, never LIVE. It never places an order, never calls a broker, never blocks an exit, and the AI places
  nothing. The load is read-only apart from what a login writes: a session, a login event, an audit row, and a new-device
  notification inside the load-test organisation.
- It never changes another organisation, a feature flag, `.env`, a secret, `SECRETS_ENCRYPTION_KEY`, or a Postgres
  role or password. Two read-only digests check this. The feature-flag row and every other organisation with its users
  (ids, names, status, plan, emails, roles, active) are compared before and after. A real sign-up or login during the
  run can change the second digest; the report then says "not confirmed", not "changed".
- It refuses outside market hours (unless `--force-offhours`), and on anything but a PAPER-only Phase 0 deployment.
- It runs as `atp`, never root, and holds the deploy lock.

## Cleanup, and how to stop it

**Ctrl-C stops it.** So does `kill -INT <pid>` from another session, or a closed SSH session (SIGHUP). Every exit runs
the cleanup: the normal end, a failure, Ctrl-C, SIGTERM, or SIGHUP. While the cleanup runs, Ctrl-C is ignored, and its
docker commands run in their own session, so a second Ctrl-C cannot cut it short. After SIGHUP the output goes to
`.deploy/loadtest/<stamp>/run.log`. A `kill -9` cannot be trapped: use the manual cleanup.

Steps:
1. Stops the samplers. Removes every client container (label `atp.loadtest=<stamp>`) and checks that none is left.
2. `backend/scripts/loadtest_org.py cleanup --stamp <stamp> --verify-audit` deletes **every row that names the
   organisation or one of its users**: sessions, login events, notifications, meters, PAPER accounts, and anything else.
   It finds them from the live schema: every `tenant_id` / `user_id` column and every foreign key to `tenants` or
   `users`. A table added later is covered.
3. **The append-only ledgers are never touched**: `audit_logs`, `audit_anchors`, `orders`, `order_events`, `trades`.
   The application role may not delete from them (`backend/scripts/db_roles.sql`), and the audit log is a
   tamper-evident hash chain. Each login appended an audit row that names its user and the organisation (foreign keys
   RESTRICT). So:
   - Those users, and the organisation, stay as **inert tombstones**. Users are deactivated, with their password
     replaced by the hash of a random secret nobody holds. The organisation is `suspended`, with the reason
     "loadtest <stamp>: synthetic organisation, cleaned up".
   - Users no ledger row names are deleted outright.
   - One audit row `loadtest_org_cleaned` records the cleanup.
4. It counts again and prints:
   - what was deleted per table;
   - what was kept on purpose (the ledgers);
   - the tombstone: status, users, 0 active, 0 usable passwords;
   - what is left over (must be none);
   - the audit chain check, from its latest anchor.

   "cleanup verified" means nothing is left but the ledgers and the inert tombstones.
5. Deletes the accounts file.

Redis keys from the run (rate-limit windows, caches) expire by themselves. Access tokens issued in the run stop
working at once: the users are inactive and their sessions are deleted.

## Manual cleanup (if the cleanup ever fails)

As `atp`:

```
/opt/atp/deploy/atp.sh loadtest-cleanup --list          # every load-test organisation: status, users, active users
/opt/atp/deploy/atp.sh loadtest-cleanup 20261021-101500  # one run (its stamp), idempotent
/opt/atp/deploy/atp.sh loadtest-cleanup --all            # every load-test organisation and every client container
```

It cleans only organisations named `loadtest-<stamp>` whose members are all `lt-<stamp>-NNNN@example.com`. Any other
organisation is listed as "never touched".

If `atp.sh` itself cannot run:

```
# 1. the client containers
docker rm -f $(docker ps -aq --filter label=atp.loadtest)
# 2. the organisation (the running release's image; STAMP from `ls /opt/atp/.deploy/loadtest/`)
cd /opt/atp && ATP_RELEASE=$(cut -c1-12 .deploy/current) docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  -f docker-compose.hostinger.yml -f deploy/compose.release.yml -p atp run --rm --no-deps -T backend \
  python scripts/loadtest_org.py cleanup --stamp STAMP --verify-audit
# 3. the accounts file
rm -f /opt/atp/.deploy/loadtest/*/accounts.csv
```

If even that fails, first make the users unusable. Then send the error. Never delete rows by hand: the audit chain
must stay intact.

```
cd /opt/atp && docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hostinger.yml -p atp \
  exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "update users set is_active = false where email like '"'"'lt-%@example.com'"'"' and tenant_id in (select id from tenants where name like '"'"'loadtest-%'"'"')"'
```
