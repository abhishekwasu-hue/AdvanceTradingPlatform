# 500-user load test (Phase 0 exit criterion "load test passed")

**Verdict: SLO FAIL on the KVM 2 shape (runs 12 and 13, CI).** Run 12 (13 Oct) is the first run
on the deciding shape as intended - the API, worker, Postgres, Redis and Caddy on 2 CPUs of 2 physical cores, inside an
NSE session (the real clock, 12:12 IST), 500 sessions, all logged in, 0 server errors. p95 per key route: dashboard
reads 67-250 ms, proposals / positions / PAPER account 106-181 ms (inside 0.5 s); chart 593 ms and screener 846 ms
(over 0.5 s, inside their documented 1.0 s and 2.0 s); login 11.4 s (over both its 0.5 s and its documented 1.0 s: the
owner's decision, OPEN_QUESTIONS LD-9). Run 13 (19 Oct, after main's merge and the changes below, on the shifted clock)
was worse on every profile: chart 2.0 s (since #249 a cold chart waits for a second broker token, LD-10), screener
1.08 s, dashboard 97-411 ms, login 28.2 s with 24 time-outs; a local A/B of the same changes put the reads at 73-206 ms
and the screener at 0.73 s. Below: "Run 12", then "After run 12 (19 Oct)".

Before run 12: run 10 (13 Oct, the first run inside a session) failed on every route on the quota shape; run 11 placed
the server on CPUs 0-1, most likely one physical core's two threads, and failed harder; runs 1-9 ran outside a session
on a lighter load.

The bar (coordinator, 13 Oct) is the owner's production box, a Hostinger KVM 2 (2 vCPU, 8 GB): the run that decides the
CI job runs as that box does. Runs 8-10 approximated it with a CPU quota on the API alone; from run 11 the API, worker,
Postgres, Redis, Caddy and the frontend share 2 pinned CPUs and the load clients use the other 2 (below). The 4-vCPU
runner without a cap is kept as a secondary run. The SLO limits are unchanged from the first run (below).

## Setup

- **Stack.** The production compose files plus `deploy/ci/compose.ci.yml`, with images built from the commit; from
  run 11 inside an NSE session (the real clock between 09:30 and 14:00 IST, else shifted by libfaketime): Caddy over
  HTTPS, then the API (`API_WORKERS` processes), Postgres 17, Redis 7, the worker and a fake Upstox / Dhan broker. Memory limits, Postgres settings and pool sizes come from `docker-compose.hostinger.yml`; the
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
    clients and the fake broker to the other CPUs (`stack.sh pin kvm2` from run 12: two CPUs on two physical cores;
    run 11 used CPUs 0-1, `pin 0,1 2,3`).
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

**SLO** (docs/SLO.md SLO-1, `loadtest.py`). From 20 Oct (owner, LD-9) a run has two verdicts and needs both:

- **API**: every scenario but login - p95 at or under 500 ms per read route (chart 1,000 ms and screener run 2,000 ms,
  their documented limits); server errors at most 0.5% of the API requests.
- **Login**, measured apart: p95 at or under 2,000 ms at 500 concurrent sessions, and no failed login (a time-out, a
  5xx, a 429 or a refusal each fails it).

The report's first line names both (`**SLO PASS** (API PASS, login PASS (p95 ... ms of 2,000; 0 failed of 500, 0
allowed); ...)`), and the job `load-500` passes or fails on the same rule. Runs 1-14 below were judged on the earlier
rule - login at 1,000 ms inside one verdict, server errors over all requests - so their verdicts are not comparable;
their numbers are.

LD-10 (owner, 20 Oct), from the first run after it: the API's broker budget stays at 1 call a second per credential and
a chart spends fewer calls instead - history and today's candles are one shared copy per broker, symbol, interval and
request window for every user (one broker fetch, through the requesting user's own session; today's copy expires when
its forming bar closes), the chart's last price comes from the shared live feed when it has the symbol, and the budget's
per-second rate is a token bucket with a burst of 2 (`app/market_data/api_budget.py`, `app/market_data/shared_candles.py`).
A cold chart's two calls no longer wait for the next second, and in this profile (500 users, 10 symbols) the charts
make about one history fetch per symbol every 5 minutes and one fetch of today's bars per symbol a minute, instead of
two calls per chart.

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
2. **The KVM 2 shape by CPU placement** (`deploy/ci/stack.sh pin 0,1 2,3` in run 11, `pin kvm2` from run 12): the
   API, worker, Postgres, Redis, Caddy and the frontend on two CPUs; the 25 load clients (`loadtest.py --pin-cpus`) and
   the fake broker on the others. It decides the job. Runs 8-10's quota shape stays as a secondary run (`load-quota`), and so does the whole runner.
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

[38352433995](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38352433995), job `load-500`,
887c744, 05:44-06:30 UTC; primary on the real clock (11:16 IST, inside the session). p50 / p95 / p99 ms:

| Profile | Shape | Chart | Screener | Login | Dashboard p95 (5 reads) | Proposals / positions / paper p95 | req/s | Server errors |
|---|---|---|---|---|---|---|---|---|
| **Primary (decides)** | pinned: API, worker, Postgres, Redis, Caddy, frontend on CPUs 0-1 | 210 / 1,429 / 2,389 | 385 / 2,146 / 2,889 | 3,131 / 30,010 / 30,024 | 161 / 527 / 576 / 607 / 660 | 464 / 301 / 496 | 76.2 | 0.23% (86 logins timed out at 30 s) |
| Login burst | same | 448 / 2,625 / 4,042 | 763 / 4,123 / 5,933 | 6,177 / 30,060 / 30,072 | 269 / 949 / 1,022 / 1,156 / 1,239 | 852 / 685 / 1,004 | 58.0 | 0.71% (104 logins timed out, 1 error) |
| Busy (think 10 s) | same | 969 / 3,328 / 5,095 | 1,596 / 3,923 / 5,899 | 30,006 / 30,020 / 30,034 | 794 / 1,983 / 1,907 / 2,027 / 2,077 | 1,374 / 1,355 / 1,557 | 95.1 | 0.96% (315 without a login) |
| KVM 2 as a quota | 2 processes, `--cpus 2`, rest unpinned | 147 / 1,446 / 2,973 | 243 / 2,192 / 3,107 | 580 / 7,008 / 9,914 | 135 / 654 / 695 / 730 / 820 | 500 / 346 / 536 | 89.6 | 0.00% |
| Whole runner | 3 processes, no cap | 109 / 737 / 1,687 | 186 / 1,195 / 2,182 | 342 / 3,429 / 5,956 | 97 / 266 / 304 / 334 / 380 | 285 / 165 / 271 | 90.4 | 0.00% |

Primary, after the login ramp only: chart p95 1,352 ms, screener 1,965 ms, dashboard 149-628 ms. Server side: login
12.1 s mean (waiting for a bcrypt thread 11.1 s mean, the check 749 ms mean against 0.28 s of CPU on an idle core),
pooled-connection wait 22.5 ms mean; containers' CPU mean / peak: API 81% / 186%, Postgres 15 / 38, fake broker
19 / 63, Caddy 9 / 18, worker 7 / 55 (% of one CPU).

Reading run 11:

- **The pinned run is harsher than the 2 cores it stands for.** The runner's 4 vCPUs are 2 physical cores with 2
  threads each on GitHub's hosts, and CPUs 0 and 1 are usually one core's two threads: the server side had about one
  core. The local pinned runs (4 vCPUs without SMT, so 2 real cores) put the reads at 0.13-0.23 s p95 and the chart at
  0.43-0.57 s. From run 12, `stack.sh pin kvm2` picks two CPUs on two physical cores from the topology and the job
  prints it.
- **Login cannot keep up on 2 vCPUs.** 500 checks in 150 s at the unchanged cost is 0.94 CPU of bcrypt for the whole
  ramp; at nice +5 bcrypt gets the CPU the reads leave, and the queue grew past the clients' 30 s timeout for 86
  users. Running bcrypt at nice 0 instead (local L7, below) did not help login (p95 11.7 s) and put the reads at
  1.7-1.8 s: the CPU is not there either way. Login is the owner's decision (What remains, OPEN_QUESTIONS LD-9).
- Inside a session every profile is slower than runs 8-9 were outside one, on every shape.

### Local runs after 887c744 (2 pinned cores, market open; 150 s after the ramp)

The same local setup as L1 (above). Foreign CPU: the other agents' work measured on CPUs 0-1 over the run (`/proc/stat`
minus the stack's own processes).

| # | When (UTC) | Code | Chart p50 / p95 / p99 | Screener | Login | Dashboard p95 | req/s | Server errors | Foreign CPU on 0-1 |
|---|---|---|---|---|---|---|---|---|---|
| L3 | 05:39-05:48 | 887c744 (300 s after the ramp) | 447 / 9,179 / 15,669 | 776 / 11,105 / 17,135 | 4,268 / 13,826 / 16,698 | 5.6-8.6 s | 79.7 | 0.00% | not measured (machine 83% busy) |
| L4 | 05:48-05:54 | 6e63404 | 196 / 668 / 1,151 | 459 / 1,455 / 2,960 | 14,503 / 30,005 / 30,013 | 85-261 ms | 72.2 | 0.17% (43 logins timed out) | 84 s |
| L5 | 05:54-06:00 | 948e32d | 119 / 426 / 735 | 256 / 898 / 1,439 | 539 / 10,155 / 12,933 | 56-162 ms | 81.0 | 0.00% | 28 s |
| L6 | 06:01-06:07 | 948e32d | 121 / 574 / 1,058 | 264 / 936 / 1,669 | 438 / 14,138 / 16,676 | 64-228 ms | 80.5 | 0.00% | 46 s |
| L7 | 06:31-06:37 | 948e32d, bcrypt at nice 0 (experiment) | 148 / 2,599 / 4,241 | 295 / 4,091 / 7,728 | 464 / 11,656 / 14,196 | 325-1,832 ms | 78.5 | 0.00% | 68 s |

After the ramp: chart p95 387 (L1), 677 (L4), 348 (L5), 595 (L6) ms; screener 677, 1,554, 591, 892 ms.

L3 ran 887c744 as pushed, whose screen ran each symbol's pandas step on its own thread (up to 3 CPU-bound threads per
run); "idle in transaction" connections averaged 12.5 against 4-5 in the other runs. With foreign load unmeasured it
cannot be told apart from noise, but more CPU-bound threads per process is the known way to slow the event loop (run 6),
so 948e32d runs a run's pandas steps in turn on one thread. L5 and L6 (948e32d) against L1 and L4 (6e63404): the
screener's p50 fell from 386-459 ms to 256-264 ms; its p95 stayed above 0.5 s (0.90-0.94 s), the chart's p95 was 0.43
and 0.57 s.

### Run 12 (the KVM 2 shape on two physical cores; the screen's pandas on one thread, 948e32d)

[38357848445](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38357848445), job `load-500`,
44cbad6, 06:39-07:24 UTC; primary on the real clock (12:12 IST, inside the session), `stack.sh pin kvm2`. p50 / p95 /
p99 ms, from the job's annotations (the job log answers 403 and the artifact download is blocked from the agents'
network, so the server-side tables of this run are not in this report):

| Profile | Shape | Chart | Screener | Login | Dashboard p95 (5 reads) | Proposals / positions / paper p95 | req/s | Server errors |
|---|---|---|---|---|---|---|---|---|
| **Primary (decides)** | pinned: API, worker, Postgres, Redis, Caddy on 2 CPUs of 2 cores | 101 / 593 / 1,101 | 173 / 846 / 1,404 | 932 / 11,430 / 13,787 | 67 / 175 / 203 / 215 / 250 | 181 / 106 / 177 | 90.2 | 0.00% |
| Login burst | same | 123 / 800 / 1,666 | 210 / 1,446 / 2,398 | 4,213 / 15,627 / 22,529 | 104 / 303 / 345 / 352 / 384 | 322 / 196 / 296 | 68.8 | 0.00% |
| Busy (think 10 s) | same | 829 / 3,519 / 5,230 | 1,224 / 4,290 / 6,692 | 25,725 / 30,015 / 30,021 | 426 / 2,156 / 2,199 / 2,157 / 2,210 | 1,860 / 1,743 / 2,174 | 124.2 | 0.57% (243 without a login) |
| KVM 2 as a quota | 2 processes, `--cpus 2`, rest unpinned | 104 / 694 / 1,153 | 165 / 1,043 / 1,700 | 434 / 5,213 / 5,870 | 77 / 202 / 241 / 255 / 283 | 217 / 133 / 202 | 90.5 | 0.00% |
| Whole runner | 3 processes, no cap | 89 / 409 / 1,115 | 150 / 760 / 1,795 | 343 / 2,080 / 4,444 | 62 / 129 / 141 / 153 / 167 | 121 / 91 / 114 | 90.8 | 0.00% |

Against run 11 (same profile, the server on one core's two threads): chart p95 1,429 -> 593 ms, screener 2,146 -> 846
ms, dashboard 161-660 -> 67-250 ms, logins that timed out 86 -> 0 (p95 11.4 s). The two-core placement and 948e32d (a
screen's pandas on one thread) are both in it. The CI lint job failed on 44cbad6 (shellcheck SC2295 in
`stack.sh kvm2_cpus`, fixed in f4bf9ac); the backend tests passed.

## After run 12 (19 Oct)

### Merged main

origin/main into this branch (f2c7f05): #249 shared LTP budget, #251 / #255 / #259 charts, #254 AI filter, #256 re-exit
guard, #258 H-C4. Conflicts: the worker (main's side: its #221 changes are this branch's, plus per-tenant savepoints
and `close_group`), the candles route's imports (both), ROADMAP_STATUS (main's page; the load-test row carries run 12).
One alembic head: this branch's c5f7e9a1b3d4 (the dashboard indexes) now revises main's e2a4c6e8f0b3. This branch adds
no flag, so `DEFAULT_OFF_FLAGS` is main's.

### Local runs on 19 Oct, and why their numbers are not results

The same local setup as L1-L7 (2 pinned real cores for the server side, CPUs 2-3 for the 25 clients and the fake
broker), with one difference: the market was closed, so the API, the worker and the fake broker ran on a clock shifted
to 11:00 IST with libfaketime, as the CI does outside a session. Every such run collapsed - before and after the merge
and before and after the changes below (B0 f2c7f05, B1 f4bf9ac, N1 / N2 2d30252): 320-390 of 500 logins timed out at
30 s and the dashboard reads took 5-8 s at p95, at 24-29 req/s against L5's 81.

Two causes, both found by measuring:

1. **libfaketime made the API's threads spin.** With the monotonic clock left real (`FAKETIME_DONT_FAKE_MONOTONIC=1`),
   libfaketime's "forced monotonic fix", which it switches on by itself on glibc 2.30 and later, gives every timed
   wait on a monotonic condition variable a wrong deadline; CPython's GIL waits on one. Three Python threads contending
   for the GIL: 0.7 s of CPU without the preload, 9.5 s with it, 7.3 s with the fix forced on, 0.8 s with
   `FAKETIME_FORCE_MONOTONIC_FIX=0`. In the API (an event loop, bcrypt threads and pandas threads per process) that was
   about 44 ms of CPU per request against L5's about 10 ms (per-thread CPU over run N2: event loops 255 s, worker
   threads 170 s, bcrypt 145 s for 9,600 requests). **The CI's shifted clock had the same setting**: a load run outside
   09:30-14:00 IST would have measured it. Runs 10-12 ran on the real clock and are not affected. Fixed in a82c49e
   (`deploy/ci/compose.ci.yml`, tested).
2. **Logins queued on the audit chain's lock while holding pool connections.** A login appends a hash-chained audit row
   under one advisory lock for the whole table, held until its transaction commits; the session row and the token were
   written inside that window. Under CPU pressure each await in it stretched, the logins waited on the lock with their
   connections checked out, and every other route waited for a connection (pool checkout 0.7-0.9 s mean against
   0.3-0.6 ms; connections "idle in transaction" 15-17 mean against 3.5). The lock wait across all local runs is
   bimodal - about 1 ms in the healthy runs (L1, L5, L6, L7), 150-280 ms mean in the collapsed ones - and L3 on 13 Oct,
   which the earlier notes could not explain, is one of the collapsed. 2d30252 writes the audit row last, right before
   the commit (N1: lock wait 83 ms mean on the still-spinning clock). Same rows, same transaction, bcrypt unchanged.

A run after the faketime fix (B2) had another agent's test suite on the server CPUs (264 s of foreign CPU on CPUs 0-1
in a 344 s run, against 15-28 s before) and is not usable.

**A/B after the faketime fix** (local, the shifted clock with `FAKETIME_FORCE_MONOTONIC_FIX=0`, back to back, 150 s
after the ramp; foreign CPU on the server cores 102 s and 132 s of about 345 s, so read the difference, not the level).
p50 / p95 ms:

| Run | Code | Dashboard p95 (5 reads) | Proposals / positions / paper p95 | Chart | Screener | Login (timed out) | Pool checkout wait mean | Audit lock wait mean | req/s |
|---|---|---|---|---|---|---|---|---|---|
| B3 | f2c7f05 (merge, before the changes) | 488 / 1,508 / 1,538 / 1,635 / 1,680 | 1,254 / 949 / 1,249 | 1,284 / 3,661 | 411 / 3,589 | 729 / 30,004 (32) | 116 ms | 2.4 ms | 74.3 |
| N3 | 6b90273 (the changes) | 73 / 149 / 153 / 170 / 206 | 187 / 98 / 152 | 1,164 / 1,606 | 224 / 727 | 9,832 / 30,004 (28) | 0.9 ms | not in the top statements | 74.1 |

With the changes the reads stay inside 0.5 s and the screener's p95 falls from 3.6 s to 0.73 s; login waits for bcrypt
instead (bcrypt thread wait 11.4 s mean against 5.4 s: the reads go first, as designed in 4f3250a), and the same number of
logins time out - the capacity question of LD-9. The chart's p50 is over 1 s in both: see "The chart's budget floor".

### The chart's budget floor (#249)

Since #249 (merged from main) every broker call of `/api/market-data/candles` takes a token from the credential's API
budget (`app/market_data/api_budget.py`): for Dhan and Zerodha the API's share is 1 call a second per credential
(`api_limits("dhan")`: per_second 1). A chart makes 2 calls (the history and today's bars, side by side since 887c744),
so the second waits for the next second: a cold chart cannot answer in under about 1 s. Every load-test user is a Dhan
session and every chart is cold for its user (BT1b: per-user caches), so chart p50 went from 0.1-0.16 s (L1-L7, run 12)
to 1.1-1.3 s (B3, N3, run 13), on every shape. The budget protects the broker allowance the worker's exits need
(ADR-0004); this branch does not change it. OPEN_QUESTIONS LD-10 puts the options to the owner.

### Changes after run 12

| Commit | Change | Measured |
|---|---|---|
| f4bf9ac | `stack.sh kvm2_cpus`: the core key quoted (shellcheck SC2295; the lint job on 44cbad6) | shellcheck clean |
| 2d30252 | A screen run's funnel shares one memo of each symbol's evaluated nodes with the full evaluation (`runtime.ScreenMemo`; off for cross-sectional screens), on one worker-thread hop (`routes.screen_and_funnel`) | the load test's 5-symbol screen: 34 -> 17 ms of evaluation CPU; matches and funnel identical on 7 kinds of screen, both evaluation paths |
| 2d30252 | The vectorised-flag read moved before the fetch | one pool checkout and one query fewer per screen run |
| 2d30252 | `resolve_many` reads and writes the session's symbol set once (`SessionSymbols.admit_many`) | 10 -> 2 Redis round trips before a 5-symbol screen's fetch; the same answers as `admit` in turn (tested) |
| 2d30252 | The worker's BYOB fan-in reads every session's open positions with one select per cycle, grouped by (organisation, user) | run 10's 27,000 `trades` reads were 500 selects a cycle (one per user); now 1 a cycle; each session still gets only its own user's rows (tested) |
| 2d30252 | Login: the audit row last, right before the commit | the lock covers the audit read, its insert and the commit only |
| a82c49e | The CI's shifted clock with `FAKETIME_FORCE_MONOTONIC_FIX=0` | 3 contending threads: 9.5 s -> 0.8 s of CPU |

Profiled, not changed: on an idle core a cold 5-symbol screen costs about 7 ms of pandas framing, 17 ms of evaluation
(was 34), 10 ms of the broker client's bar parsing and 7 ms of candle-cache serialising on the event loop; the
vectorised evaluation (flag `screener_vectorized`, off) would take the evaluation to about 6 ms - the owner's call, not
turned on here. A screen-result cache (`app/screener/result_cache.py`) would rarely hit in this profile (each user's
bars are cached under their own session, BT1b, and each run picks 5 of 10 symbols at random) and is not wired in.

### Run 13 (this branch after the merge and the changes above)

[39270301515](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/39270301515), job `load-500`,
6b90273, 14:35-15:19 UTC, triggered by the push (no manual dispatch). The first load run on the shifted clock (real
20:07 IST, outside the session; the stack at 10:00 IST) - with a82c49e's faketime setting, which was verified against
libfaketime's source build here; the Debian package in the CI image could not be checked from the agents' network.
p50 / p95 / p99 ms, from the job's annotations:

| Profile | Shape | Chart | Screener | Login | Dashboard p95 (5 reads) | Proposals / positions / paper p95 | req/s | Server errors |
|---|---|---|---|---|---|---|---|---|
| **Primary (decides)** | pinned: API, worker, Postgres, Redis, Caddy on 2 CPUs of 2 cores | 1,136 / 2,031 / 3,224 | 190 / 1,078 / 2,082 | 1,855 / 28,238 / 30,011 | 97 / 321 / 356 / 360 / 411 | 297 / 187 / 342 | 85.1 | 0.06% (24 logins timed out) |
| Login burst | same | 1,224 / 2,775 / 4,181 | 253 / 1,594 / 2,298 | 4,851 / 30,023 / 30,058 | 253 / 1,018 / 1,009 / 1,031 / 1,068 | 573 / 345 / 617 | 65.5 | 0.16% (26 without a login) |
| Busy (think 10 s) | same | 1,914 / 5,633 / 8,407 | 993 / 3,602 / 5,041 | 30,005 / 30,018 / 30,055 | 1,177 / 3,145 / 2,901 / 2,957 / 3,160 | 2,390 / 2,246 / 2,389 | 113.7 | 0.64% (250 without a login) |
| KVM 2 as a quota | 2 processes, `--cpus 2`, rest unpinned | 1,142 / 2,447 / 3,927 | 178 / 1,611 / 2,954 | 702 / 6,417 / 7,885 | 169 / 653 / 693 / 749 / 878 | 463 / 244 / 367 | 89.0 | 0.00% |
| Whole runner | 3 processes, no cap | 1,108 / 1,873 / 2,837 | 162 / 969 / 2,136 | 340 / 3,306 / 5,534 | 115 / 285 / 341 / 362 / 391 | 275 / 159 / 249 | 89.5 | 0.00% |

**Run 14** ([39277548322](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/39277548322), a0f05b3 -
the same application code as run 13, its tests and this report changed; push-triggered, shifted clock, real 20:55 IST):

| Profile | Chart | Screener | Login | Dashboard p95 (5 reads) | Proposals / positions / paper p95 | req/s | Server errors |
|---|---|---|---|---|---|---|---|
| **Primary (decides)** | 1,126 / 2,209 / 3,727 | 165 / 1,042 / 1,793 | 1,441 / 13,391 / 20,447 | 140 / 520 / 564 / 594 / 662 | 393 / 253 / 446 | 89.1 | 0.00% (all 500 logged in) |
| Login burst | 1,202 / 2,917 / 3,688 | 217 / 1,619 / 3,280 | 4,562 / 20,058 / 29,953 | 260 / 821 / 842 / 817 / 915 | 596 / 466 / 489 | 67.4 | 0.03% |
| Busy (think 10 s) | 1,725 / 5,277 / 7,678 | 934 / 3,186 / 4,446 | 30,005 / 30,019 / 30,036 | 1,015 / 2,877 / 2,854 / 2,871 / 2,975 | 2,056 / 1,856 / 2,312 | 116.6 | 0.63% (253 without a login) |
| KVM 2 as a quota | 1,118 / 1,881 / 2,480 | 168 / 1,148 / 1,686 | 500 / 5,877 / 7,992 | 91 / 260 / 304 / 329 / 360 | 270 / 170 / 259 | 89.4 | 0.00% |
| Whole runner | 1,111 / 1,754 / 3,011 | 143 / 914 / 1,724 | 348 / 3,620 / 5,304 | 96 / 215 / 250 / 268 / 303 | 240 / 129 / 195 | 89.7 | 0.00% |

Runs 13 and 14 on the same code differ by about 2x on login (28.2 s / 24 time-outs against 13.4 s / none) and by
0.1-0.25 s on the reads: the runner's variance is as large as the effects being looked for.

Runs 13 and 14 are worse than run 12 on every profile, the uncapped runner included, and the local A/B above points the other
way, so the difference is not the changes alone. What is known: the chart's floor is #249's budget (p50 0.1 -> 1.1 s on
every shape, above). What is not separated: the rest of main's merge (B3 is the merged code and collapsed locally), the
shifted clock (first load run on it; the CI image's libfaketime not verified), and the runner (hosted runners vary by up
to about 2x, "Results"). A run on the real clock inside 09:30-14:00 IST on the same commit separates the clock; it was
not started here (at most one run per lane turn, and the next session opens after the 20 Oct holiday).

CI on 6b90273 ([39270302008](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/39270302008)):
lint, frontend, perf and look-ahead passed; the backend job failed on two of this branch's new tests (shared state in
the full suite: one session key used by two registries, and a whole-chain audit check on a chain other tests break on
purpose - both fixed in a0f05b3, whose CI run 39277547649 passes them) and on `test_contract_notes.py` (2 tests), which fails the
same way on main (045da0c, run 39257366188) and passes alone: not from this branch.


### Review fixes (PR #269)

- **B1 (exits, ADR-0004): the shared Dhan symbol map now refreshes on a miss.** Since 6e63404 a process kept one symbol
  map for 6 hours, so a strike listed after it was built could not be resolved - no quote, no LIVE exit order - until
  the map expired. Now a miss rebuilds the map once when it is older than 60 s; a symbol still missing after a rebuild
  is not looked for again for 10 minutes (a bogus symbol polled every cycle does not download the master each time),
  and one unknown symbol in a quote batch is left out instead of failing the batch (alone, it is the error as before).
- **N2 (exits): orders on their own connection pool.** The shared per-process Dhan client had one 100-connection pool
  for everything; a LIVE exit order could wait behind data fetches up to the 15 s pool timeout. Order and account calls
  (`/orders`, `/trades`, `/positions`, `/holdings`, `/fundlimit`, `/profile`) and the scrip-master download (an exit's
  instrument may need it) now use a second shared client (`shared_http.shared_client(pool="orders")`); quotes, bars and
  option chains stay on the data pool. Tested with 150 data calls holding the data pool: an order and a cancel answer.
- **N8: `BCRYPT_NICE` that is not a whole number** falls back to 5 with a warning instead of stopping the API at import.
- **Follow-ups (re-check at bdabb70):** a failed master download (503, timeout) is not tried again for 60 s - misses
  fail at once meanwhile, a symbol the map has still resolves, and a lookup that has a map never waits behind a build
  in progress; an order (`place_order`, an exit among them) skips the remembered misses and gets its rebuild (still at
  most one a minute); expired remembered misses are pruned on every write; the master's CSV is parsed on a worker
  thread.

None of these changes the load test's verdict: the SLO still fails (run 14: login p95 13.4 s, chart 2.2 s, screener
1.04 s).


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
   it); now one slim symbol map per process, rebuilt every 6 hours and on a miss (at most once a minute; review fix B1, below).
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
- **The worker inside a session**: done in 2d30252 (one positions select per cycle for every session).
- **Chart p95 on the KVM 2**: run 12 593 ms; since #249, at least about 1 s for every cold chart on a Dhan or Zerodha
  session (the budget floor above; run 13 2.0 s). The owner's call, LD-10.
- **Screener p95 on the KVM 2** (run 12 846 ms, run 13 1,078 ms; local N3 727 ms): inside its documented 2.0 s, over
  the owner's 0.5 s. The next levers, measured but not taken: the vectorised screen path (a flag, the owner's call) and
  moving the broker client's bar parsing and the candle-cache serialising off the event loop.
- **Run 13's regression against run 12**: a run on the real clock inside a session on the same commit would separate
  the shifted clock from main's merge and the runner.
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
