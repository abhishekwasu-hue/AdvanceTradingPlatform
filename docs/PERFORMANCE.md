# Performance (section 54, Z10-d)

Two kinds of numbers live here:
- the screener's **G4** targets (ATP_SCREENER_SPEC §1.5), measured in the CI `perf` job on every push;
- the **API under load**: a session-shaped load test, run by CI with 20 users against the in-process test app, and
  by the owner with 500 users against staging.

Nothing here is ever run against the production VPS. `scripts/loadtest.py` refuses any host that is not localhost or
a staging host.

## G4: screener targets (CI `perf` job)

| Target (§1.5) | CI runner, median of 5 | Budget | Per-symbol runtime (flag off) |
|---|---|---|---|
| EOD screen, 2,500 symbols x 50 factors, daily bars | **0.38-0.67 s** | 3 s | 50-82 s (estimated: 50 symbols timed, then x 50) |
| The same screen again, from the result cache | **2-3 ms** (key: universe + as_of); **14-26 ms** (key: bar fingerprints) | 0.2 s | same |
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
  reason. A symbol alone in its index group (a new listing, a suspension gap) also runs per symbol.
- **Few symbols, many bars.** For a frame with fewer symbols than bars, the kernels hand the work to pandas column by
  column, which is cheaper there.
- **Speed-up.** About 130x for the 2,500-symbol EOD screen and about 7x for a bar-close cycle (50 symbols per rule)
  on the CI runners.
- **Opt-in, behind the operator flag `screener_vectorized` (off by default).** When it is on, the screener run
  (`/api/screener/run`, including the U5 funnel) and the bar-close engine pass `vectorized=True`. OPEN_QUESTIONS Z10-d1
  proposes turning it on.

**The result cache (`app/screener/result_cache.py`).** It works as the spec describes:
- the key is a hash of the AST, params, timeframe, universe and as_of;
- with no as_of, the key uses a fingerprint of each symbol's bars instead: count, first and last bar time, and the last
  bar's values;
- entries are bounded, expire after a TTL and are returned as copies.

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

The command is `python scripts/loadtest.py --in-process --users 20 --seconds 20 --ramp 10 --think 1 --annotate`.
The job fails when the SLO is missed.

Run of 2026-10-11 (GitHub-hosted runner): 20 users (5 accounts) for 30 s, 3,198 requests, 102 requests/s, 0 server
errors, **SLO PASS**.

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

### The owner: 500 users against staging

```
cd backend
# accounts.csv: about 8 staging accounts, one "email,password" per line. The first user of each account logs in, and
# the login limit is 10 a minute per IP.
python scripts/loadtest.py --base https://<staging host> --users 500 --seconds 600 --ramp 120 --think 2 \
    --accounts accounts.csv --json > loadtest-500.json
```

- **Hosts.** The script accepts localhost, or a host whose name contains `staging`. A staging host with another name
  must be listed in `LOADTEST_ALLOWED_HOSTS=<host>`. Never list the production VPS.
- **Run length.** Keep `--ramp` + `--seconds` under 14 minutes. Access tokens live 15 minutes, and later requests
  would answer 401.
- **Flags and data.** Turn `screener_v2` on for the staging tenants first, or the screener scenario is reported as
  "disabled". The same goes for a broker session for the chart and screener scenarios: without one they are
  reported as "unavailable".
- **What to watch.**
  - p95 per scenario and the server-error rate;
  - the API's CPU and the Postgres connections in use (`pg_stat_activity`);
  - whether the worker's cycle time (`atp_worker_cycle_duration_seconds`) moves. The API and the worker are separate
    processes, so it should not.
- **Results.** Paste the table here with the date.

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

The checks now run on the thread pool (`security.verify_password_async` / `hash_password_async`). bcrypt releases the
GIL while it works. The answers are unchanged, and the timing parity for unknown e-mails is kept: the dummy hash is
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
