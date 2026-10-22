# Performance (section 54, Z10-d)

Two kinds of numbers live here:
- the screener's **G4** targets (ATP_SCREENER_SPEC §1.5), measured in the CI `perf` job on every push;
- the **API under load**: a session-shaped load test, run by CI with 20 users against the in-process test app, and
  with 500 users on the CI staging stack on every push to main (`docs/reports/load_test_500.md`), and once by the owner
  on the Hostinger host (`deploy/ci/loadtest_500.sh`).

Nothing here is ever run against the production VPS. `scripts/loadtest.py` refuses any host that is not localhost or
a staging host.

## G4: screener targets (CI `perf` job)

| Target (§1.5) | CI runner, median of 5 | Budget | Per-symbol runtime (flag off) |
|---|---|---|---|
| EOD screen, 2,500 symbols x 50 factors, daily bars | **0.38-0.67 s** | 3 s | 50-82 s (estimated: 50 symbols timed, then x 50) |
| The same screen again, from the result cache (**not wired yet**: no route or job calls the cache; OPEN_QUESTIONS Z10-d2) | **2-3 ms** (key: universe + as_of); **69 ms** (key: bar fingerprints, every value hashed; 14-26 ms when only the last bar was) | 0.2 s | same |
| Bar-close scan, 200 symbols (4 rules x 50), 5-minute bars, through the bar-close engine | **0.10-0.20 s** | 1 s | **0.66-1.44 s** (over the target on the slower runners) |

These are the ranges over the CI runs of 2026-10-11 (GitHub-hosted `ubuntu-latest`). The calibration took 0.057-0.083 s,
so the CI factor was 1.00-1.03. Each run writes its numbers as check-run notices (`G4 ...`), so the latest figures can be
read from the `perf` check without the job log.

**How it is measured** (`backend/scripts/bench_screener.py`, gated by `backend/tests/test_z10d_perf.py`):
- **Synthetic universe.** Seeded random-walk bars. No price is written down anywhere. There is one shared bar index
  per universe, like an exchange's EOD file or one bar-close cycle.
  - EOD: 300 daily bars, enough for the 200-bar factors.
  - Bar-close: 5 sessions of 5-minute bars, with the clock set just after the last bar closed.
- **50 factors.** Trend, momentum, volatility, range, volume and count filters at several windows. Each one is a
  filter that most symbols pass.
- **The bar-close figure** is the engine itself: `evaluate_rule` for 4 rules of 50 symbols (the per-rule cap), with
  the cycle cache and the events written to the database. The broker fetch is a stub, since its latency is the
  broker's.
- **Median of 5** timed runs after one warm-up run, so one slow run does not fail the job.
- **The CI factor.** The script times a fixed numpy / pandas workload that does not use the screener. The factor is
  that time divided by the same workload's time on the reference runner (0.08 s), and it is never below 1. Every
  budget is the target times the factor, and the factor is printed. A slower machine therefore gets a proportionally
  larger budget, and a faster one never a tighter one.
- **Full sizes.** The design note asked for a CI job "at small N". The vectorised runtime makes the specified sizes
  cheap, so the job runs them as specified and nothing is extrapolated. `ATP_PERF_SYMBOLS` /
  `ATP_PERF_BARCLOSE_SYMBOLS` give a smaller universe for a quick local run. The budget then scales with the count,
  which is stricter than the target, because the fixed cost of a run does not shrink.
- **Job cost.** The `perf` job runs in parallel with the other jobs and takes about 1.2 minutes end to end (pip
  install from cache, about 15 s of G4 timings, the 30 s load test). It does not lengthen the pipeline's critical
  path: the frontend and backend jobs take longer.

```
cd backend
python scripts/bench_screener.py                 # the table above, on this machine (PASS / FAIL per target)
python scripts/bench_screener.py --per-symbol --symbols 100 --repeat 1
ATP_PERF=1 python -m pytest tests/test_z10d_perf.py -s
```

### What makes the targets: the vectorised runtime (`app/screener/panel.py`)

The per-symbol runtime pays pandas' fixed cost per operation for every symbol and every factor. A 50-factor screen
over 2,500 symbols is about 125,000 small operations, roughly a minute and a half. The vectorised runtime stacks the
symbols that share a bar index into one frame per field (one column per symbol) and runs each operation once.

- **Exact.** pandas computes rolling means, rolling standard deviations and exponential means column by column with
  an online, compensated update. The kernels in `panel.py` make the same floating-point operations in the same order,
  one bar at a time for all symbols together, so each column is the series pandas returns for that symbol.
  `tests/test_z10d_vectorised_screen.py` checks this:
  - bit for bit, per kernel and per function, including gaps, leading gaps, repeated values and negative values;
  - match for match and reason for reason, on a seeded fuzz of 60 screens over a mixed universe.
- **Covered:** fields; SMA, EMA, RSI, ATR, ADX, PlusDI, MinusDI, Max, Min, Greatest, Least, Count, CountStreak,
  CrossAbove, CrossBelow, PctChange, ZScore, Lag; arithmetic, comparisons, BETWEEN / IN and ALL / ANY / NOT, on the
  screen's base timeframe. A call that appears twice in a screen is computed once.
- **Not covered,** so these run per symbol unchanged: another timeframe, Rank / PercentileRank, price-action, option
  chain and classifier functions. A group whose evaluation raises also falls back, and each symbol then gets its own
  reason; the fallback is logged as a WARNING with the exception type, so a kernel problem does not go unseen. A symbol alone in its index group (a new listing, a suspension gap) also runs per symbol.
- **Few symbols, many bars.** For a frame with fewer symbols than bars, the kernels hand the work to pandas column by
  column, which is cheaper there.
- **Speed-up.** About 130x for the 2,500-symbol EOD screen and about 7x for a bar-close cycle (50 symbols per rule)
  on the CI runners.
- **Opt-in, behind the operator flag `screener_vectorized` (off by default).** When it is on, the screener run
  (`/api/screener/run`, including the U5 funnel) and the bar-close engine pass `vectorized=True`. OPEN_QUESTIONS Z10-d1
  proposes turning it on.

**The result cache (`app/screener/result_cache.py`).** It works as the spec describes, with a namespace in front:
- the key is a hash of the namespace, AST, params, timeframe, universe and as_of;
- the namespace is required by the API: (tenant id, the user id when the data tier is per user (BT1), the data source
  / tier), so one organisation's or one user's result is never served to another;
- `as_of` is valid only for the shared EOD lake, whose bars for a closed session never change. Bars read through a
  broker session use the fingerprint instead;
- with no as_of, the key uses a fingerprint of each symbol's bars: count, first and last bar time, the columns and a
  hash of every value (so a revised old bar is a new key too);
- entries are bounded, expire after a TTL and are returned as copies; the hit and miss counters are kept under the
  lock.

It is the "cached < 200 ms" path for an EOD batch screen. That batch run (a lake-backed screen over the full universe)
does not exist yet. OPEN_QUESTIONS Z10-d2 covers where it plugs in.

## Load test: session-shaped (`backend/scripts/loadtest.py`)

**What each virtual user does.** It acts like a person with the app open:

1. It logs in once. The first user of each account logs in; the other users share that login.
2. It then loops, and each loop does the following:
   - **Dashboard poll:** the five read paths the shell polls, sent together as a browser does: `/api/auth/me`,
     `/api/deployments`, `/api/notifications`, `/api/system/status` and `/api/portfolio/exposure`.
   - **Proposal list**, half of the loops: `/api/ai/actions?status=PROPOSED`.
   - **PAPER positions**, half of the loops: `/api/positions`.
   - **A chart**, a quarter of the loops: `POST /api/market-data/candles`, 5-minute bars.
   - **A screener run**, a tenth of the loops: `POST /api/screener/run` with `preview: true`. It is evaluated, never
     stored.
   - **A think time** before the next loop.

Users start spread over `--ramp`. The script places, changes and approves nothing, and it sends no order.

**SLO, per scenario.**

| Scenario | p95 limit |
|---|---|
| Every read path | 500 ms (SLO-1) |
| Login | 1,000 ms (bcrypt is slow on purpose) |
| Chart | 1,000 ms |
| Screener run | 2,000 ms |

Server errors must stay at or below 0.5% of requests. A scenario that a deployment refuses as a whole is counted and
reported, never as an error. The refusals are:
- a flag that is off (503 with `X-Feature-Disabled`);
- no broker session (409);
- rate limiting (429).

### CI: 20 users against the in-process test app

The test app is `scripts/loadtest_app.py`:
- the real app on a throwaway SQLite file;
- the auth and AI rate limits off, as in the test suite (every in-process user shares one fake IP);
- synthetic broker bars;
- `screener_v2` and `paper_account` on.

The command is `python scripts/loadtest.py --in-process --users 20 --seconds 20 --ramp 10 --think 1 --slo-factor auto
--annotate`. `--slo-factor auto` scales the p95 limits by the CI factor (the G4 calibration, never below 1), so a slow
runner does not fail the job on noise; the server-error limit is never scaled. The job fails when the SLO is missed.

Run of 2026-10-11 (GitHub-hosted runner): 20 users (5 accounts) for 30 s, 3,198 requests, 102 requests/s, 0 server
errors, **SLO PASS** for this 20-user in-process smoke run only. The Phase 0 criterion (500 users on the KVM 2 shape,
below) does **not** pass: run 14 (19 Oct) login p95 13.4 s, chart 2.2 s, screener 1.04 s (`docs/reports/load_test_500.md`).

| Scenario | n | p50 ms | p95 ms |
|---|---|---|---|
| login | 5 | 252 | 414 |
| dashboard /api/auth/me | 469 | 16 | 56 |
| dashboard /api/deployments | 469 | 25 | 77 |
| dashboard /api/notifications | 469 | 29 | 93 |
| dashboard /api/system/status | 469 | 26 | 88 |
| dashboard /api/portfolio/exposure | 469 | 33 | 114 |
| proposals | 245 | 8 | 52 |
| positions | 222 | 10 | 71 |
| paper_account | 222 | 18 | 134 |
| chart | 106 | 34 | 114 |
| screener_run (5 symbols, preview) | 53 | 34 | 77 |

**Saturation.** With half the think time (0.5 s), the same app on the same runner served 106-150 requests/s:
- the p95s rose to 150-490 ms (queueing in one process on SQLite);
- one run went over on `/api/paper/account` (520 ms).

CI uses the 1 s think time so that the job measures service time, not a saturated queue. That is also closer to how
people poll.

### 500 users on the CI staging stack (Lane D) and on the Hostinger host

The Phase 0 exit criterion "load test passed" is measured on a real stack, not the in-process app: the production
compose files plus `deploy/ci/compose.ci.yml` (Caddy over HTTPS, the API, the worker, Postgres, Redis and a fake
Upstox / Dhan broker), built from the commit. Results and analysis: `docs/reports/load_test_500.md`.

- **Where.** CI job `load-500` in `.github/workflows/staging-stack.yml` (every push to main, and on demand with
  `users` / `seconds` / `think` inputs). On the Hostinger host, one command as `atp` (runbook section 12a):
  `/opt/atp/deploy/ci/loadtest_500.sh` - its own compose project `atp-ci` beside production, loopback ports only,
  removed at the end.
- **Users.** 500 synthetic users, each in its own organisation with a fake Dhan session (Tier A), written straight into
  the stack's throwaway database (`deploy/ci/seed_users.py`). Every user logs in (500 logins over a 150 s ramp).
- **The login wave and the rate limit.** Nothing is bypassed. The generator runs as 25 shards, each its own container
  and so its own client IP on the stack network, through Caddy, which passes that IP on; each IP logs in at most
  8 times a minute against the limit of 10. `backend/scripts/loadtest.py --account-offset / --raw-out / --merge`.
- **One schedule for all shards (LD-7).** Every shard's ramp starts at the same wall-clock instant (`--start-at`) and
  shard i of n takes the i/n-th part of each start slot (`--shard / --shards`), so the 25 shards between them start a
  user every 0.3 s. Before, each shard started its 20 users on its own clock: 25 logins arrived together every 7.5 s.
- **Profiles.**
  - Primary: a page load every 30 s on average (the Dashboard page's own refresh), for 5 minutes after the ramp, in
    the KVM 2 shape (below). It decides the job.
  - Login burst and busy (a page load every 10 s, 3 minutes): the same shape, for comparison.
  - The KVM 2 as a CPU quota (runs 8-10's shape): only the API limited, `docker update --cpus 2`, everything else
    unpinned; and the whole runner (3 API processes, no limit). For comparison.
- **The KVM 2 shape (LD-7, `stack.sh pin kvm2`).** The owner's box runs the API (2 processes), the worker,
  Postgres, Redis, Caddy and the frontend on the same 2 vCPUs, so the CI pins all of them to two CPUs on two physical
  cores (on the SMT runner CPUs 0 and 1 can be one core's two threads; the job prints the topology) and the 25 load
  clients (`loadtest.py --pin-cpus`) and the fake broker to the other CPUs. A CPU quota instead (`--cpus 2`) is
  not the same machine: the API's threads spread over all 4 CPUs, spend the quota early in each 100 ms period and
  stall for the rest of it, while Postgres and the worker run on the other CPUs for free.
- **Clock (LD-7).** The job runs inside an NSE session (`ATP_CI_CLOCK=auto`: the real clock between 09:30 and 14:00
  IST on a trading day, else shifted by libfaketime). While the market is open the worker keeps every user's broker
  session (`byob_data_tier`, on in the CI posture) and the bars include the day so far; runs 1-9 ran outside a
  session and measured a lighter load.
- **Diagnostics.**
  - Every report also lists the requests started after the login ramp on their own. This table is diagnostic only;
    the verdict covers every request.
  - Database connections by state (`pg_stat_activity`) every 5 s: peak and mean per state.
  - What the API measured itself over the run (`deploy/ci/metrics_summary.py`, two scrapes of `/metrics`): the
    server-side time per route and its share of all request time, the waits for a pooled connection
    (`atp_db_pool_wait_seconds`) and for a bcrypt thread (`atp_bcrypt_queue_wait_seconds`), and the bcrypt checks' own
    time (`atp_bcrypt_seconds`).
  - The statements by total time (`pg_stat_statements`, preloaded by the CI overlay's Postgres only).
  - On request (workflow input `profile`), the CI job samples the API processes with py-spy for 60 s inside the ramp
    and 60 s after it, and prints a summary (`deploy/ci/profile_api.sh`). Off by default: it costs the runner CPU.
- **Clock.** The real clock: the load needs no market session, and libfaketime (used by the acceptance flow) costs the
  API about half its throughput in a local comparison.

The manual form against any staging host still works (`--base https://<staging host> --accounts accounts.csv`); keep
`--ramp` + `--seconds` under 14 minutes (access tokens live 15).

### API processes (LD-7)

The API runs `API_WORKERS` uvicorn processes (`backend/scripts/serve_api.sh`, the image's command):

| Where | Default | Why |
|---|---|---|
| `docker-compose.yml` (dev, plain compose) | 1 | as before |
| `docker-compose.hostinger.yml` | 2 | one per vCPU on the KVM 2 |
| `deploy/ci/compose.ci.yml` | 3 | 4-vCPU runner, shared with the load generator, the fake broker and Postgres |
| `deploy/ci/loadtest_500.sh` (owner, on the host) | 2 | the API as production runs it there |

One process is one event loop, so it uses about one core however many the host has.

What each process keeps for itself, checked for N processes:
- **Shared already (Postgres / Redis):** the rate limits, the worker replica lock, the Phase 0 lock (re-read on use),
  sessions and token revocation, the market-data caches, and the start-up flag seed (a second process's insert race is
  handled).
- **Per process, safe as a copy:**
  - the tenant key ring: a key never changes once created, and a master-key rotation re-wraps it without changing it;
  - the journal flag cache (10 s TTL);
  - the status-page cache (15 s);
  - the chart layer caches (keyed by their data);
  - the public `/api/system/status` (5 s, dropped by an admin change in the process that made it).
- **Not shared out:** bcrypt's thread limit is the CPU count of each process (at least 1), not divided by
  `API_WORKERS` (LD-9): the processes do not get the logins evenly (Caddy's keep-alive connections each stay with one
  process), and with one thread per process a login waited behind its own process's check while a CPU was free.
  The kernel shares the CPUs between the checks; `BCRYPT_NICE` keeps them behind the other requests.
- **Merged:** with more than one process, `PROMETHEUS_MULTIPROC_DIR` is set and emptied at start-up. `/metrics`
  (whichever process answers) sums the counters and histograms, and gives each gauge its most recently set value.
- **Pools:** each process has its own database pool (`DB_POOL_SIZE` + `DB_MAX_OVERFLOW`: 5 + 10 by default, 10 + 5
  on Hostinger). On Hostinger that is API 2 x 15 + trading worker 15 + research worker 15 = 60, under Postgres'
  `max_connections` of 75 (12 left for backups, restores and psql beside the 3 reserved). The 10 are kept warm: with
  5 + 5 the overflow connections were closed at every check-in and opened again, and a request waited for a new
  connection (14.5% of the 500-user test's request time on the KVM 2 shape, `atp_db_pool_wait_seconds`).
  `pool_pre_ping` stays: a connection left dead by a database restart is replaced, not answered with a 500.

**No pool connection is held across a slow step.** `app.db.session.release_connection` ends a read-only transaction
before bcrypt (login, sign-up, invite, password reset/change, MFA disable), before a chart's broker calls, and before a
screener run's broker calls and its evaluation. Before this, a login held its connection through the bcrypt queue,
and a chart held its connection through the broker. A local 500-user run ended with all 15 connections "idle in
transaction" and 1,411 pool time-outs.

**The event loop does no pandas work for a chart or a screen.** The chart's resampling and serialising, and the
screener's per-symbol frames, run on worker threads. The request-body cap is a pure ASGI middleware, not
`BaseHTTPMiddleware`.

**The route scan is skipped for paths already resolved** (`app/core/route_cache.py`). FastAPI asks each of the 98
top-level routes in turn whether it matches, and an included router answers by scanning its own routes: 1.5-3.5 ms per
request for routes near the end of the list, 12.7% of the API's CPU after the ramp on the KVM 2 shape. The cache keeps,
per (method, root path, path) without path parameters, the index of the top-level entry FastAPI's own loop picked, and
next time asks that entry first and handles it the way the loop does. No full match, a 404, a 405, a path with
parameters or a changed route list go through FastAPI's loop unchanged. It is only installed when the installed
FastAPI's loop is the one it mirrors (checked at start-up), and `tests/test_ld7_route_cache.py` resolves every served
path both ways.

**Broker connections are reused** (`app/brokers/shared_http.py`). An adapter is built per request; each built its own
HTTP client, so every chart and screener run paid a new TLS context and handshake, and the client was never closed.
Adapters built without a client now share one per broker host and event loop. It carries no credential (they go per
request) and keeps no cookie at all, so nothing of one user's session reaches another's request. Dhan uses it so far.

**Dhan's scrip master is read once per process**, not per request: the symbol map (`_resolve`'s three fields per
symbol) is built from one download and kept for 6 hours. Every chart and screener run used to download and parse the
whole public master (tens of MB on the real host; small on the CI fake, so the test did not show it).

### Bottlenecks found and fixed

**`/api/paper/account` counted proposals in four queries.** It was the slowest read in the sessions: p50 265 ms
against 120-230 ms for the dashboard reads in the saturated run. The four proposal counts (approved, rejected,
pending, expired unanswered) are now one aggregate query, so the route makes five database round trips instead of
eight. The numbers are the same, tested against the four separate counts. The gain was not measured on its own: the
next CI run also moved to the 1 s think time.

**bcrypt held the event loop.** Every login ran `bcrypt.checkpw` inline in its async route, a quarter of a second or more of CPU. The same was true
of registration, password reset and change, invite acceptance and MFA disable. While it ran, every other request of
that API process waited.

Measured in the test app with 4 logins at once while another client polled `/api/auth/me` every 20 ms:

| | `/me` polls answered during the logins | Longest `/me` |
|---|---|---|
| Before | 23 | 1,018 ms (a whole bcrypt check) |
| After | 64 | 497 ms (CPU contention on a loaded 4-vCPU sandbox) |

The checks now run on worker threads (`security.verify_password_async` / `hash_password_async`) under bcrypt's own
`anyio.CapacityLimiter`, sized to the CPUs the process may use: more checks at once than CPUs only slow each one, and
a login wave no longer takes the shared thread pool that sync routes use. bcrypt releases the GIL while it works. The answers are unchanged, and the timing parity for unknown e-mails is kept: the dummy hash is
still checked.

At 500 users this matters most during the morning login wave.

## Phase O4 baseline (2026-09-27, single uvicorn worker, Postgres 16 + Redis on the same 2-vCPU sandbox)

The earlier probe polled the six dashboard read paths with one shared token. It is kept for comparison: the session
scenarios above include five of them.

| Route | requests | p50 ms | p95 ms | p99 ms | 5xx |
|---|---|---|---|---|---|
| /api/auth/me | 1038 | 44.0 | 131.0 | 853.2 | 0 |
| /api/deployments | 1037 | 52.9 | 126.8 | 230.9 | 0 |
| /api/trades | 1036 | 48.1 | 119.9 | 218.3 | 0 |
| /api/notifications | 1033 | 52.2 | 129.1 | 209.4 | 0 |
| /api/system/status | 1023 | 58.1 | 150.4 | 244.6 | 0 |
| /api/portfolio/exposure | 1020 | 58.2 | 134.2 | 203.3 | 0 |

20 users for 21 s: 6187 requests, 294 requests/s, 0.00% errors. **SLO-1: PASS** (worst p95 150 ms).

## What this tells us

- **Throughput.** A single API process serves a few hundred dashboard requests per second. The worker is a separate
  process, so API load never delays a trading cycle. SLO-3 is measured by `atp_worker_cycle_duration_seconds`, not
  here.
- **Levers if p95 approaches 500 ms.** The shared cost of every route is JWT verification plus the login-session
  liveness check. The first lever is `uvicorn --workers N` behind the nginx front end. The second is caching
  `/api/system/status` in Redis for a few seconds: it is unauthenticated and identical for everyone.
- **Screener and bar-close CPU.** With the per-symbol runtime, the screener and the bar-close engine are CPU-bound per
  symbol and factor. The vectorised runtime cuts that by about 7x for a bar-close cycle and about 130x for a 2,500-symbol
  EOD screen. Turning
  `screener_vectorized` on is the lever for the bar-close target.
- **Entry latency (SLO-7, signal to fill).** It depends on the broker's API round trip and is exported as
  `atp_order_entry_latency_seconds`. It cannot be load-tested without a broker sandbox.

## Re-running

- **Load test.** Run it against staging after every release that touches the request path (auth, middleware, ORM
  models), and paste the table here with the date. A regression of more than 2x on any p95 is a release blocker.
  `--json` output is suitable for a CI artefact.
- **G4.** The G4 numbers come from every CI run, in the `perf` check's notices.
