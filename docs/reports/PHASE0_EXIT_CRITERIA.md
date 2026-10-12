# Phase 0 exit criteria: status for the coordinator

Source of the criteria: `docs/specs/ATP_PHASE0_FREEZE.md` section 5. CI evidence: `.github/workflows/staging-stack.yml`
(jobs `acceptance`, `load-500`, `rollback`), first run
[38291357196](https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/actions/runs/38291357196) on 2026-10-12. The
workflow runs on every push to main, on demand, and on a branch push that changes the stack.

| Criterion | Covered in CI | Result | Still needs the server |
|---|---|---|---|
| Z1-Z11 merged | - | per `docs/ROADMAP_STATUS.md` | - |
| Acceptance flow end to end, two users on Upstox and Dhan | yes: `acceptance` | **PASS 16/16** (with the exit-sweep fix, LD-1; **FAIL** at the protective exit on main without it) | real Upstox OAuth and a real Dhan token: the CI stack uses a fake broker behind the real host names; the owner's first PAPER day check (`scripts/first_paper_day_check.py`) |
| No API order path reachable | yes: `acceptance` (fake broker records every call) + the existing Z10-c static sweep | **PASS**: 60 broker calls, 0 to an order API; a LIVE deployment is refused (503) | - (the Phase 0 lock is seeded on a Phase 0 host since #204; `atp.sh health` checks it) |
| Stale data => alerts suppressed | yes: `acceptance` | **PASS**: feed 60 min stale -> no event, no alert; current -> the same rule fires and notifies | - |
| Load test passed (500 sessions) | yes: `load-500` | **FAIL** on a 4-vCPU runner: 0 server errors, but p95 2.4-3.4 s against 0.5-2 s limits (`docs/reports/load_test_500.md`) | the Hostinger run (`deploy/ci/loadtest_500.sh`, runbook 12a) - the number that counts for the beta host |
| Rollback rehearsed | yes: `rollback` (`deploy/atp.sh` itself) | **PASS 12/12** (`docs/reports/rollback_rehearsal.md`) | one owner rehearsal with real releases on the host (runbook 9) |

Found and fixed on the way (both merged into `claude/ci-staging-stack`):

- **LD-1, a Phase 0 safety bug.** The worker priced open positions only for organisations with an ACTIVE or PAUSED
  deployment. A PAPER position from the ticket, a /paper or webhook proposal, or a stopped deployment never reached its
  stop. Fix: commit 22d337c (branch `claude/fix-exit-sweep`, can be its own PR), unit tests plus the acceptance flow.
- **LD-2, a deploy gap.** `SUPER_ADMIN_EMAILS` never reached the API container, so on Hostinger nobody could open Admin
  -> Controls or issue invite codes. Fix: passed through in `docker-compose.yml`; `.env.example` and runbook 4.

## The acceptance flow (job `acceptance`)

The stack: the production compose files (`docker-compose.yml` + `prod` + `hostinger` + `deploy/compose.release.yml`)
plus `deploy/ci/compose.ci.yml`, images built from the commit, a throwaway env file, traffic through Caddy over HTTPS.
The real Upstox and Dhan adapters talk to `deploy/ci/fake_broker.py`: inside the stack the broker host names resolve to
it and the app trusts only its throwaway CA, so no request can reach a real broker and every call is recorded. The run
was outside market hours, so the API's, the worker's and the fake broker's clocks were shifted (libfaketime) to
10:00 IST of the same trading day (`deploy/ci/ci_clock.py`); inside a session the real clock is used.

| Step | Result | Detail (run 38291357196) |
|---|---|---|
| stack ready, NSE session open | pass | deep health ok; NSE open on the stack's clock (10:02 IST) |
| admin: sign-up, MFA, Admin -> Controls | pass | SUPER_ADMIN from `SUPER_ADMIN_EMAILS`, MFA session; fresh-host defaults: `live_trading` off, `phase0_paper_only` on, `beta_cohort_gate` off |
| admin: Phase 0 flag posture | pass | `beta_cohort_gate` on + the Phase 0 features (`deploy/ci/ci_posture.py`), set through the admin API |
| admin: invite codes | pass | 2 single-use codes; cohort cap 50 |
| invite -> sign up (A, B) | pass | no code -> 403; A and B signed up (separate organisations); a used code -> 403 |
| A links Upstox (OAuth) | pass | login page -> callback -> code exchanged -> token VALID |
| B links Dhan | pass | client id + access token, profile read, token VALID |
| broker smoke tests | pass | Upstox 8/9, Dhan 7/9 steps ok (profile, funds, instruments, quote, positions, orders all ok; option-chain steps not modelled) |
| run a screen | pass | each on the user's own session (Tier A): 5/5 matched |
| PAPER ticket -> ENTRY proposal | pass | approval-only: plan -> proposal (ENTRY) |
| Approve PAPER | pass | one PAPER position each at the re-quoted price (RELIANCE 8 @ 2,780.33; INFY 13 @ 1,873.55) |
| protective exit by the worker | pass | price set below the stop; the worker closed both at the stop within 12 s (Stop Loss) |
| journal | pass | both trades in the users' journals; another user's entry -> 404 |
| no API order reaches a broker | pass | LIVE deployment refused (503); 54 broker calls, 0 to an order API |
| stale data suppresses alerts | pass | B's feed 60 min stale: the rule recorded nothing and sent nothing ("waiting for the closed bar from the broker"); current feed: 1 event, 1 SCREEN_ALERT |
| final checks | pass | still 0 order-API calls after 60 broker calls; Phase 0 lock unchanged |

Without the exit-sweep fix (origin/main's backend), the same flow stops at "protective exit by the worker": the
position stays open for 180 s with the price through its stop (local run, 2026-10-12).

## What runs where

| | CI (GitHub-hosted runner) | Hostinger (owner) |
|---|---|---|
| acceptance flow | every push to main | not needed: real-broker first PAPER day instead |
| 500-user load test | every push to main (4 vCPU runner) | `/opt/atp/deploy/ci/loadtest_500.sh` once, before the beta opens |
| rollback | every push to main, with `deploy/atp.sh` | one rehearsal with real releases (runbook 9) |
