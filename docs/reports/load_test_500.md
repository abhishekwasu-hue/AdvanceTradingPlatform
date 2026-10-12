# 500-user load test (Phase 0 exit criterion "load test passed")

**Verdict on the CI runner: SLO FAIL.** 500 synthetic sessions, all 500 logged in, 0 server errors in the primary
profile, but every route's p95 is over its limit (dashboard reads 2.4-4.9 s against 500 ms). The stack saturates at
about 80-90 requests/s on a 4-vCPU GitHub runner that also runs the load generator; the profile asks for about 110.
The Hostinger host has to be measured itself before the 500-user stage (`deploy/ci/loadtest_500.sh`, below); on this
evidence it should be expected to fail too until the API gets more capacity.

## Setup

- **Stack**: the production compose files + `deploy/ci/compose.ci.yml` (Caddy over HTTPS -> one API process -> Postgres
  17, Redis 7, the worker; a fake Upstox / Dhan broker), images built from the commit, real clock. Memory limits from
  `docker-compose.hostinger.yml` (API 1.5 GB, Postgres 2 GB, ...).
- **Runner**: GitHub-hosted `ubuntu-latest`, 4 vCPU, 15 GB. The 25 generator containers run on the same machine.
- **Users**: 500, each its own organisation with a fake Dhan session (Tier A), seeded straight into the stack's
  throwaway database (`deploy/ci/seed_users.py`).
- **Login wave**: 500 logins over 150 s, from 25 client IPs (one generator container each, through Caddy); at most
  8 logins a minute per IP against the limit of 10. Nothing is bypassed: 0 logins refused.
- **Session**: the `backend/scripts/loadtest.py` model - each loop the 5 dashboard reads in parallel, then with some
  probability the proposals, the PAPER positions and account, a chart (5-minute bars through the user's own broker
  session) and a screener preview over 5 symbols - then a think time.
- **Profiles**: primary, think 30 s (the Dashboard page's own refresh), 300 s after the ramp - it decides the job;
  busy, think 10 s, 180 s, for comparison; lever, the primary profile with 2 API processes (`uvicorn --workers 2`), an
  experiment only - production runs one.

SLO (docs/SLO.md SLO-1, `loadtest.py`): p95 under 500 ms per read route (login 1,000 ms, chart 1,000 ms, screener run
2,000 ms), server errors at most 0.5% of requests.

## Results

CI run [38294902118](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38294902118), job
`load-500`, 2026-10-12 (commit 739fde2).

### Primary profile (think 30 s): FAIL

500 users (500 logged in), 25 client IPs: 38,777 requests in 494 s (78.4 requests/s), server errors 0.00%.

| Route / scenario | n | p50 ms | p95 ms | p99 ms | p95 limit | verdict |
|---|---|---|---|---|---|---|
| login | 500 | 1,104 | 5,670 | 9,324 | 1,000 | over |
| dashboard /api/auth/me | 5,605 | 1,730 | 4,818 | 8,237 | 500 | over |
| dashboard /api/deployments | 5,605 | 1,724 | 4,954 | 8,373 | 500 | over |
| dashboard /api/notifications | 5,605 | 1,720 | 4,921 | 8,338 | 500 | over |
| dashboard /api/portfolio/exposure | 5,605 | 1,749 | 4,875 | 7,411 | 500 | over |
| dashboard /api/system/status | 5,605 | 1,729 | 4,826 | 7,406 | 500 | over |
| proposals | 2,794 | 1,511 | 4,582 | 7,366 | 500 | over |
| positions | 2,760 | 1,450 | 4,525 | 8,021 | 500 | over |
| paper_account | 2,760 | 1,518 | 4,662 | 6,730 | 500 | over |
| chart | 1,404 | 1,390 | 5,935 | 10,009 | 1,000 | over |
| screener_run | 534 | 2,004 | 5,764 | 7,999 | 2,000 | over |

CPU while it ran (docker stats every 10 s; 100% = one vCPU): API mean 107% (peak 301%), fake broker 41%, Postgres
11%, Caddy 11%, worker 0%, the 25 generator containers about 2-4% each (about 75% together).

The first run of the same profile (run
[38291357196](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38291357196), commit df1f92e) was
faster: 86.5 requests/s, dashboard p95 2.86-2.91 s, login 4.5 s, chart 3.0 s, screener 3.4 s, also 0 server errors -
the same verdict; hosted runners vary by up to 2x between runs.

### Busy profile (think 10 s, for comparison): FAIL

28,881 requests in 351 s (82.3 requests/s); dashboard p50 about 10 s, p95 about 14.5 s; 0.28% server errors and
transport time-outs (2 users could not log in within the 30 s client time-out). Saturated: throughput did not rise
with the demand (82 vs 78 requests/s).

### Lever: 2 API processes (experiment, think 30 s): FAIL, better

42,607 requests in 495 s (86.0 requests/s), server errors 0.00%; dashboard p95 2.35-2.44 s (p50 350-430 ms), login
5.1 s, chart 2.9 s, screener 4.7 s. API mean 127% CPU (peak 285%), Postgres 30%, fake broker 50%.

## Why it fails, and what would move it

1. **One API process is one core.** The API runs a single uvicorn process (`backend/Dockerfile`), so its Python event
   loop uses about one vCPU however many the host has (mean 107-112% above, the peaks are bcrypt threads). Demand at
   think 30 is about 500 x 6.9 requests / 31 s = 110 requests/s; the stack served 78-87.
2. **The database pool holds connections across slow non-database work.** Each API process has SQLAlchemy's default
   pool (5 + 10 = 15 connections, 30 s wait). A request keeps its connection from its first query to the end of the
   request, through bcrypt in login and through the broker calls of a chart or a screener run. In a local reproduction
   (120 users, 4 vCPU sandbox) `pg_stat_activity` showed all 15 connections "idle in transaction" for seconds at a time
   while the API's CPU sat at 4% - every other request waited for a connection.
3. **The runner is shared with the generator.** About 0.75 vCPU of the 4 go to the 25 generator containers and about
   0.4 to the fake broker, so the CI numbers are pessimistic for the API itself. The Hostinger run has the same
   constraint (generator on the host) but its own CPUs and disk.

Levers, in the order this evidence suggests (none is changed here - each is its own reviewed change, OPEN_QUESTIONS
LD-7):

- release the database connection before bcrypt and before broker I/O (login, chart, screener run) - or size the pool
  for the concurrency (`pool_size`, `max_overflow`; Postgres allows 60 on the Hostinger overlay);
- run 2-3 API processes on the host (`uvicorn --workers`); the experiment shows a gain, limited here by the shared
  runner. Check first that every startup task is safe to run once per process;
- cache `/api/system/status` (public, identical for everyone, polled on every page) in Redis for a few seconds.

## Re-running

- **CI**: every push to main; Actions -> "Staging stack (Phase 0 exit criteria)" -> Run workflow, with `users`,
  `seconds` and `think` inputs.
- **Hostinger (owner, one command, as `atp`, when no beta user is on)**: `/opt/atp/deploy/ci/loadtest_500.sh` - its own
  stack beside production (compose project `atp-ci`, loopback ports only, removed afterwards); the report lands in
  `~/atp-loadtest/load-hostinger.md`. Runbook section 12a, `docs/PERFORMANCE.md`.
- **What the Hostinger run adds**: the real host's CPUs (2 vCPU KVM), disk and memory limits, the same Caddy, and the
  number that decides the 500-user stage. Paste its table below with the date.

### Hostinger result

Not run yet (the server is an owner blocker).
