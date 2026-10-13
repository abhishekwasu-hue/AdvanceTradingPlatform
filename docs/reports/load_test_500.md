# 500-user load test (Phase 0 exit criterion "load test passed")

**Verdict: SLO FAIL on the KVM 2 shape (run 10, CI, 13 Oct).** 500 sessions, all logged in, 0 server errors, but
with the market open every route but the public status read was over 0.5 s at p95 (chart 2.19 s, screener 3.69 s,
login 8.90 s). Run 10 was the first run inside an NSE session: runs 1-9 had all run outside one (below, "Run 10: the
market was open"), so run 9's 0.52 s chart and 0.91 s screener were measured on a lighter load. A local cross-check on
2 pinned cores (API, worker, Postgres and Redis sharing them, market open) put chart at 0.41 s, screener at 0.75 s and
the dashboard reads at 0.13 s or less, login at 8.7 s. Run 11 (the next commit, below) decides on the KVM 2 shape
by CPU placement instead of a CPU quota, inside a session.

The bar (coordinator, 13 Oct) is the owner's production box, a Hostinger KVM 2 (2 vCPU, 8 GB): the run that decides the
CI job runs as that box does. Runs 8-10 approximated it with a CPU quota on the API alone; from run 11 the API, worker,
Postgres, Redis, Caddy and the frontend share 2 pinned CPUs and the load clients use the other 2 (below). The 4-vCPU
runner without a cap is kept as a secondary run. The SLO limits are unchanged from the first run (below).

## Setup

- **Stack.** The production compose files plus `deploy/ci/compose.ci.yml`, with images built from the commit; from
  run 11 inside an NSE session (the real clock between 09:30 and 14:00 IST, else shifted by libfaketime): Caddy over HTTPS, then the API (`API_WORKERS` processes), Postgres 17, Redis 7, the worker and a fake
  Upstox / Dhan broker. Memory limits, Postgres settings and pool sizes come from `docker-compose.hostinger.yml`; the
  CI overlay only adds `pg_stat_statements` to Postgres (a test pins the rest of its command to the KVM 2's).
- **Runner.** GitHub-hosted `ubuntu-latest`, 4 vCPU, 15 GB. The 25 generator containers and the fake broker run on the
  same machine.
- **Users.** 500, each its own organisation with a fake Dhan session (Tier A), seeded straight into the stack's
  throwaway database (`deploy/ci/seed_users.py`).
- **Login wave.** 500 logins over 150 s from 25 client IPs (one generator container each, through Caddy), at most
  8 logins a minute per IP against the limit of 10. Nothing is bypassed. Since d9a8f89 the shards share one schedule
  (below, "The generator change").
- **Session.** The `backend/scripts/loadtest.py` model. Each loop: the 5 dashboard reads in parallel; with some
  probability the proposals, the PAPER positions and account, a chart (5-minute bars through the user's own broker
  session) and a screener preview over 5 symbols; then a think time.
- **Profiles** (job `load-500`, from run 11):
  - **Primary, decides the job:** think 30 s (the Dashboard page's own refresh), 300 s after the ramp, the KVM 2
    shape: 2 API processes; the API, worker, Postgres, Redis, Caddy and frontend pinned to CPUs 0-1, the 25 load
    clients and the fake broker to CPUs 2-3 (`stack.sh pin 0,1 2,3`).
  - Login burst (secondary): the same shape, the shards in step - 25 logins at the same instant every 7.5 s, a
    market-open rush. 60 s after the ramp.
  - Busy (secondary): think 10 s, 180 s, the same shape. Twice the primary's demand.
  - KVM 2 as a quota (secondary, runs 8-10's primary): 2 API processes capped with `docker update --cpus 2`,
    everything else unpinned.
  - Whole runner (secondary): the primary profile with 3 processes and no CPU cap.
  - Run 10 decided on the quota shape; before 6e63404 the primary was the whole runner.
- **Diagnostics** (every run): the requests started after the ramp on their own; database connections by state every
  5 s; the API's own time per route and its waits for a pooled connection and for a bcrypt thread (two `/metrics`
  scrapes, `deploy/ci/metrics_summary.py`); the top statements (`pg_stat_statements`). py-spy samples the API when the
  workflow's `profile` input is on (off by default: it costs the runner CPU).

**SLO** (docs/SLO.md SLO-1, `loadtest.py`): p95 at or under 500 ms per read route; login 1,000 ms, chart 1,000 ms,
screener run 2,000 ms; server errors at most 0.5% of requests. The owner's "p95 at or under 0.5 s on the key routes" is
met where the tables say so. Login's documented limit is 1.0 s, chart's 1.0 s and the screener run's 2.0 s: the
coordinator puts that distinction to the owner, and the job judges the documented limits.

## Results

Hosted runners vary by up to about 2x between runs (compare runs 3 and 4: nearly the same code), so read the trend, not
one row. Dashboard = the 5 dashboard reads (lowest: the cached public `/api/system/status`; highest:
`/api/portfolio/exposure`).

### KVM 2 as a CPU quota (2 API processes, `--cpus 2`) - decided the job in run 10, secondary from run 11

| # | CI run | Commit | Dashboard p95 | Proposals / positions / paper p95 | Chart p95 | Screener p95 | Login p95 | Server errors |
|---|---|---|---|---|---|---|---|---|
| 1 | [38294902118](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38294902118) | 739fde2 | 2.35-2.44 s (2 processes, no cap) | - | 2.9 s | 4.7 s | 5.1 s | 0.00% |
| 8 | [38331552960](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38331552960) | 4f3250a | 53-159 ms | 105 / 70 / 109 ms | 477 ms | 860 ms | 3,688 ms | 0.00% |
| 9 | [38336696823](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38336696823) (py-spy on) | 84b07d0 | 69-225 ms | 134 / 97 / 152 ms | 521 ms | 906 ms | 1,337 ms | 0.00% |
| 10 | [38342119221](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38342119221) (**market open**) | 6e63404 | 140-943 ms | 564 / 322 / 679 ms | 2,188 ms | 3,691 ms | 8,899 ms | 0.00% |

### The whole runner (3 API processes, no CPU cap) - decided the job up to run 9

| # | CI run (job `load-500`) | Commit | What changed | Dashboard p95 | Login p95 | Chart p95 | Screener p95 | req/s | Server errors |
|---|---|---|---|---|---|---|---|---|---|
| 1 | [38294902118](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38294902118) | 739fde2 | baseline: 1 API process | 4.82-4.95 s | 5.67 s | 5.94 s | 5.76 s | 78.4 | 0.00% |
| 2 | [38309116992](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38309116992) | b0235fa | 3 processes; the connection released before bcrypt and broker calls; 5 s public status cache; 2 indexes | 1.41-2.87 s | 8.89 s | 5.75 s | 7.02 s | 83.7 | 0.00% |
| 3 | [38313426522](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38313426522) | 66398a5 | pure ASGI body cap; chart and screener pandas on threads | 0.10-0.48 s | 4.67 s | 1.47 s | 2.64 s | 90.5 | 0.00% |
| 4 | [38316903558](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38316903558) | 24f8046 | diagnostics (py-spy sampling during the run) | 1.07-2.72 s | 14.7 s | 6.66 s | 8.35 s | 83.6 | 0.00% |
| 5 | [38320430320](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38320430320) | 7806c08 | `bars_to_dataframe` 4x faster; `_frame_to_bars` without iterrows | 0.35-1.44 s | 9.77 s | 3.49 s | 5.07 s | 88.3 | 0.00% |
| 6 | [38323198976](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38323198976) | 18179e6 | CPU-bound threads limited to 2 per process (reverted in 7) | 0.48-1.59 s | 8.15 s | 3.79 s | 6.34 s | 87.6 | 0.00% |
| 7 | [38326207275](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38326207275) | 08d07fc | fake broker caches its bars per minute; py-spy off | 0.13-0.55 s | 7.20 s | 1.46 s | 2.52 s | 90.4 | 0.00% |
| 8 | [38331552960](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38331552960) | 4f3250a | bcrypt on its own threads at nice +5 | 32-77 ms | 2.85 s | 284 ms | 390 ms | 91.8 | 0.00% |
| 9 | [38336696823](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38336696823) | 84b07d0 | the shards on one schedule (d9a8f89); bcrypt share rounded up (b10c39d); wait metrics; py-spy on | 51-153 ms | 1.24 s | 456 ms | 739 ms | 91.0 | 0.00% |
| 10 | [38342119221](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38342119221) | 6e63404 | warm pools, route-scan cache, shared broker connection, Dhan symbol map; **inside an NSE session** (the first run that was) | 130-475 ms | 4.12 s | 1.04 s | 1.93 s | 89.9 | 0.00% (1 login) |

Runs for b10c39d, d9a8f89 and 55ba22a were cancelled by the next push before their load job ran (the workflow keeps
one run per branch).

## Run 10: the market was open

Run 10 ([38342119221](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38342119221), job
`load-500`, 04:13-04:48 UTC = 09:43-10:18 IST) was worse than run 9 on every route and on both shapes, with the code
of 6e63404 that should only have saved CPU. The difference is the clock: the job ran on the real clock (`ATP_CI_CLOCK:
real`), and run 9 ran at 08:09-08:44 IST, before the open, like every earlier run. Inside a session:

- the worker keeps each user's own broker session open (`byob_data_tier`, on in the CI posture) and reads every
  user's open positions each cycle: 27,000 `trades` reads in the primary run, its CPU 8% mean / 60% peak against
  0-2% in run 9; the fake broker's CPU went from 13-14% to 18-20%;
- the bars include the day so far, and the candle cache's entries end with the forming bar;
- and with bcrypt sized for the 2-CPU cap (6e63404, 1 thread per process instead of 2) the login queue grew through
  the whole ramp: waiting for a bcrypt thread 982 ms mean (run 9: 15 ms), checks 432 ms mean.

### Run 10 in full (every profile, p50 / p95 / p99 ms)

| Profile | Shape | Chart | Screener | Login | Dashboard p95 (5 reads) | Proposals / positions / paper p95 | req/s | Server errors |
|---|---|---|---|---|---|---|---|---|
| Primary (decides) | KVM 2 as a quota: 2 processes, `--cpus 2` | 151 / 2,188 / 4,736 | 301 / 3,691 / 5,988 | 548 / 8,899 / 12,753 | 140 / 930 / 856 / 891 / 943 | 564 / 322 / 679 | 88.9 | 0.00% |
| Login burst | same | 231 / 2,462 / 4,272 | 490 / 4,201 / 5,244 | 3,778 / 12,132 / 15,099 | 227 / 1,230 / 1,250 / 1,362 / 1,324 | 1,039 / 750 / 821 | 67.5 | 0.01% (2 logins) |
| Busy (think 10 s) | same | 3,626 / 14,645 / 24,877 | 4,244 / 15,782 / 27,905 | 10,758 / 30,009 / 30,014 | 1,135 / 9,942 / 9,483 / 9,292 / 9,740 | 8,165 / 8,265 / 8,488 | 117.2 | 0.17% (43 logins timed out) |
| Whole runner | 3 processes, no cap | 134 / 1,038 / 1,758 | 283 / 1,927 / 3,281 | 368 / 4,123 / 5,088 | 130 / 336 / 397 / 408 / 475 | 353 / 225 / 336 | 89.9 | 0.00% (1 login) |

Dashboard order: `/api/system/status`, `/api/auth/me`, `/api/deployments`, `/api/notifications`,
`/api/portfolio/exposure`. Primary, server side: waiting for a pooled connection 71 ms mean (run 9: 8 ms), connections
"idle in transaction" peak 27 (run 9: 17), the API at 96% CPU mean of its 200% quota and 203% peak.

### Local cross-check (2 pinned cores, market open)

The KVM 2 by CPU placement, on this development container (4 vCPU, 16 GB): the API (2 uvicorn processes, uvloop, as
`serve_api.sh` runs it), the trading worker (cycle 10 s, as in CI), Postgres 16 (the KVM 2 overlay's settings: shared
buffers 512 MB, max_connections 75, pg_stat_statements) and Redis (384 MB, volatile-lru) all pinned to CPUs 0-1
(`taskset`); the CI fake broker and 25 load clients (one loopback address each, so one client IP each) on CPUs 2-3; the
same seed (500 users, a fake Dhan session each, the CI flag posture) and `loadtest.py` profile (think 30 s, ramp 150 s,
300 s after it). No Caddy (the clients talk HTTP to the API). Real clock, inside the session.

**Noise:** the container is shared with other agents, whose test runs used 1-2 CPUs during these runs and are not
pinned away from CPUs 0-1 (load average 4-6; whole-machine CPU 60% mean in L1, 84% in L2). Read the rows as a
cross-check of the shape, not as the host's numbers.

| # | When (UTC) | Commit | Change | Chart p50 / p95 / p99 | Screener | Login | Dashboard p95 | Proposals / positions / paper p95 | req/s | Server errors |
|---|---|---|---|---|---|---|---|---|---|---|
| L1 | 05:08-05:17 | 6e63404 | - | 158 / 406 / 544 | 386 / 753 / 1,017 | 637 / 8,745 / 10,617 | 42-127 ms | 109 / 60 / 100 | 90.5 | 0.00% |
| L2 | 05:19-05:28 | 6e63404 | bcrypt 2 threads per process (experiment) | 787 / 5,404 / 8,258 | 1,716 / 7,475 / 11,074 | 2,826 / 30,005 / 30,011 | 3.4-5.1 s | 3.5 / 3.4 / 3.7 s | 75.4 | 0.13% (49 logins timed out) |

L1 server side: pooled-connection wait 0.3 ms mean, bcrypt thread wait 1.29 s mean, bcrypt check 487 ms mean. L2 ran
while another agent's test suite took CPUs 0-1 (its bcrypt checks took 1.19 s mean against L1's 0.49 s), so it says
nothing about the bcrypt thread count either way; it is listed because it was run.

What L1 shows: on 2 pinned cores, with Postgres and the worker on them too, the reads and the chart are inside 0.5 s at
p95 and the screener is not (0.75 s); login is not near its limit. The quota shape of the CI's primary run (run 10) is
harsher than 2 real cores: the cap lets the API's threads (2 event loops, 2 bcrypt threads, the pandas threads) spread
over 4 CPUs and then stalls all of them for the rest of each 100 ms period, while Postgres and the worker run beside
it for free.

### Changes after run 10 (this commit)

1. **The CI runs inside an NSE session** (`ATP_CI_CLOCK: auto` for `load-500`): the real clock between 09:30 and
   14:00 IST on a trading day, else shifted into a session by libfaketime (as the acceptance job), so the job no
   longer measures a lighter load when it happens to start before 09:15 IST. The report's first line names the clock.
2. **The KVM 2 shape by CPU placement** (`deploy/ci/stack.sh pin 0,1 2,3`): the API, worker, Postgres, Redis, Caddy
   and the frontend on CPUs 0-1; the 25 load clients (`loadtest.py --pin-cpus`) and the fake broker on CPUs 2-3. It
   decides the job. Runs 8-10's quota shape stays as a secondary run (`load-quota`), and so does the whole runner.
3. **A screen run fetches its symbols side by side**, at most 3 at a time and never more than the broker's burst
   allowance (`app/brokers/rate_budget.py`: 3 for Dhan, Zerodha and any broker it does not list), in the symbols'
   order. Every load-test user's first screen is a cold fetch (BT1b: each user's bars are cached under their own
   session only): 5 sequential broker round trips (about 1 s at the first request on a warm process, 350 ms after,
   locally) become 2 waves.
4. **A chart's two broker calls run side by side** (`MarketDataService._fetch`: the history and the day so far); an
   error from either still fails the fetch, history's first.
5. **The per-route p50 / p95 / p99 are also annotations** on the job (`::notice title=<profile> p50/p95/p99 ms`), so
   they can be read without the job log.

Not changed, considered: a longer cache for completed daily bars. Per user (BT1b), 400 days of daily bars are about
35 KB a symbol; 500 users x 10 symbols for an hour would be about 175 MB of Redis's 384 MB.

### Run 11 (the KVM 2 shape by CPU placement, inside a session)

Not finished when this was written (the job runs on the commit that adds this section); its numbers follow in the next commit.


## The generator change (d9a8f89) - what it changed and how much of the gain it is

**What changed.** Each of the 25 shards started its 20 users at `ramp x i / 20` from its own start, and the shards
were started together: the logins arrived as about 25 at once every 7.5 s, not 500 evenly over 150 s as the profile
and `loadtest.py`'s docstring say. How close together depended on how fast `docker compose run` brought the containers
up, a few seconds apart. Now every shard's ramp starts at one wall-clock instant (`--start-at`) and shard k takes the
k/25-th part of each 7.5 s slot (`--shard k --shards 25`): one login every 0.3 s.

**What did not change.** 500 logins in 150 s, 25 client IPs, at most 8 logins a minute per IP against the limit of 10,
the same bcrypt cost, the same rate limits, the same think time, the same SLO.

**How much of the gain it is - the same commit, the same KVM 2 shape** (run 9, 84b07d0):

| Arrival of the logins | Login p50 | Login p95 | bcrypt: waiting for a thread (mean) | Chart p95 | Screener p95 | Dashboard p95 |
|---|---|---|---|---|---|---|
| Even, one every 0.3 s | 276 ms | 1,337 ms | 15 ms | 521 ms | 906 ms | 69-225 ms |
| 25 at the same instant every 7.5 s (burst) | 2,891 ms | 5,607 ms | 1,859 ms | 1,448 ms | 2,343 ms | 90-601 ms |

The burst run is harsher than the runs before d9a8f89, whose shards started a few seconds apart by accident: the
KVM 2 shape's login p95 was 3.69 s in run 8 (4f3250a). So of run 8 -> run 9's KVM 2 login p95 (3.69 s -> 1.34 s),
the generator explains most; the server changes between them (the bcrypt share, the wait metrics) are small. A burst
of N logins clears in about N x 0.25 s / the bcrypt threads, which is the burst row: that is how login behaves in a
real market-open rush, and why the burst stays as a secondary run. The burst run had 1 server error (a login) in
17,629 requests.

## Where the time went (top 5, measured on the runner, KVM 2 shape, run 9)

All server-side request time in the run: 2,350 s (the API's own histograms). py-spy at 50 Hz, 60 s in the ramp and
60 s after it.

| # | Cost | Share | What it is |
|---|---|---|---|
| 1 | bcrypt during the login ramp | 74% of the API's busy CPU in the ramp; login 9.0% of request time | The check itself 310 ms mean, p95 about 1 s (its nice +5 thread waits for CPU); waiting for a thread 15 ms mean. |
| 2 | Waiting for a pooled database connection | 14.5% of request time (340 s; mean 8 ms, p95 under 50 ms, p99 under 250 ms) | With 5 + 5 per process the overflow connections were closed at every check-in and opened again. |
| 3 | FastAPI route matching | 12.7% of busy CPU after the ramp | Every request scanned the top-level routes in order (1.5-3.5 ms for a route late in the list). |
| 4 | Chart (`/candles`) | 14.5% of request time, 12.7% of CPU after the ramp | Pandas resample and frames about 8%; a new HTTP client and TLS handshake to the broker per request about 3%. |
| 5 | Auth and dependencies per request | `get_current_user` 7.4% of CPU after the ramp | JWT decode 2.1% and two lookups per request (the user, the session). Not changed: a session cache would delay a revocation. |

Next after these: the screener run, 8.8% of request time (mean 334 ms). Python's garbage collector did not show among
the costs.

## What each fix did, and why

1. **Connections held across slow steps** (b0235fa). The local reproduction ended in 1,411 `QueuePool` time-outs, with
   all 15 connections "idle in transaction" while the API's CPU was idle: a login held its connection through the
   bcrypt queue, a chart or a screener run through the broker calls. `release_connection` now ends the read-only
   transaction first.
2. **One API process** (b0235fa). One event loop uses about one core. Now `API_WORKERS` processes: 2 on the KVM 2
   (docs/PERFORMANCE.md "API processes").
3. **Event-loop work** (66398a5). The body-size check is pure ASGI instead of `BaseHTTPMiddleware`; a chart's
   resampling and serialising and a screen's per-symbol frames run on threads.
4. **Pandas per Python object** (7806c08). An exact integer-epoch index and column iteration instead of
   `pd.to_datetime` over tz-aware datetimes and `iterrows`; the same frames, tested across time zones, DST, duplicates
   and order.
5. **bcrypt took CPU from the open pages** (87755d8, b10c39d). bcrypt runs on its own threads at nice +5, one per CPU
   of the process's share (rounded up). The cost factor is the same and every check still runs; the reads go first.
6. **The generator** (d9a8f89): above.
7. **Warm pools** (6e63404). The KVM 2 overlay keeps 10 connections per process (+5 overflow) and Postgres allows 75:
   API 2 x 15 + the two workers' 30 = 60, 12 left for a backup, a restore, a migration or psql beside the 3 reserved,
   about 750 MB at 10 MB a backend inside Postgres' 2 GB. `pool_pre_ping` stays (a dead connection after a database
   restart is replaced, not answered with a 500).
8. **Route-scan cache** (6e63404, `app/core/route_cache.py`). Per (method, root path, path) without path parameters,
   the top-level entry FastAPI's own loop picked; the next request asks it first and handles it as the loop does.
   Everything else goes through the loop. `tests/test_ld7_route_cache.py` resolves every served path both ways.
9. **Broker connections and Dhan's master** (6e63404). One HTTP client per broker host and event loop for adapters
   built per request: credentials per request, no cookie kept (tested). Dhan's public scrip master was downloaded and
   parsed on every chart and screener run (tens of MB on the real host; the CI fake is small, so the test did not show
   it); now one slim symbol map per process, rebuilt every 6 hours.
10. **bcrypt sizing in a capped container** (6e63404): the thread count honours the cgroup CPU quota, so the 2-CPU cap
    behaves like a 2-CPU host (before, the capped API sized itself for the runner's 4 CPUs).

Also tried, and not kept: a per-process limit of 2 CPU-bound threads (run 6: no gain); a session cache in
`get_current_user` (it would delay a logout or a revocation by its lifetime).

## What remains

- **Login** (the open decision). 500 logins in 150 s is 3.3 bcrypt checks a second at the unchanged cost factor,
  about 0.25-0.43 s of CPU each: 0.8-1.4 of the KVM 2's 2 vCPUs for the whole ramp, beside the 500 sessions' reads.
  Whatever runs bcrypt, the box is at capacity during that ramp, and p95 at or under 0.5 s is not reachable without
  a lower cost factor or a session cache (both ruled out) or more CPU. Run 9's 1.34 s (2 bcrypt threads per process
  under a quota spread over 4 CPUs, before the open) is the best login p95 measured; on 2 real cores (L1, run 11) the
  login queue grows through the ramp. Options for the owner: the documented 1.0 s at a lower login rate (a 300 s ramp,
  or most users resuming a session instead of a password login - refresh tokens already do that), a KVM 4, or the
  login p95 left out of the 0.5 s bar (its documented SLO is 1.0 s). OPEN_QUESTIONS LD-9.
- **The worker inside a session** reads each user's open positions one query per user per cycle (500 a cycle in the
  test, every 10 s in CI, every 60 s in production). One query for all of them is the next cut if the KVM 2 run
  shows the worker.
- **The Hostinger run** (below) decides the 500-user stage.


## Re-running

- **CI**: every push to main; Actions -> "Staging stack (Phase 0 exit criteria)" -> Run workflow, with `users`,
  `seconds`, `think` and `profile` inputs.
- **Hostinger (owner, one command, as `atp`, when no beta user is on)**: `/opt/atp/deploy/ci/loadtest_500.sh` - its own
  stack beside production (compose project `atp-ci`, loopback ports only, removed afterwards); 2 API processes as in
  production; the report lands in `~/atp-loadtest/load-hostinger.md` with the server-side tables. Runbook section 12a,
  `docs/PERFORMANCE.md`.
- **What the Hostinger run adds**: the real host's CPUs (2 vCPU KVM, shared with the worker, Postgres and Caddy), disk
  and memory limits, the same Caddy, and the number that decides the 500-user stage. Paste its table below with the
  date.

### Hostinger result

Not run yet (the server is an owner blocker).
