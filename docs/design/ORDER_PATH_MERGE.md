# Order-path merge rule (owner, 1 Nov - DECISIONS 2026-11-01)

Order-path PR = touches orders, exits, execution, risk limits, the order ticket, the broker limiter / budgets
(M68 PR-1..9 execution / risk / ticket parts, M71 limiter, anything under backend/app/execution, risk_engine, paper,
brokers/rate_budget, workers/trading_worker exits).

One at a time:
1. Bring the branch up to date with the latest main (merge main into the branch; no history rewrite).
2. Independent review of the full diff (a reviewer who did not write it) against ADR-0004 (exits never blocked),
   ADR-0006 (AI never places / changes orders), M68 (no pre-chosen trading values), PAPER = LIVE one code path.
3. Green CI on that head (backend, frontend, acceptance, rollback where it runs).
4. Merge with the exact head sha. Only then the next order-path PR starts step 1.
5. After the merge, run the two suites below on the new main and report the counts.

## Isolation

Run both suites on their OWN Redis (several agents / sessions share the default local Redis and flush it, which makes the
worker-lock tests fail falsely - seen 1 Nov: 3 false failures on a shared Redis, 192/192 on an own one):

    redis-server --port 6390 --daemonize yes --save "" && export REDIS_URL=redis://127.0.0.1:6390/0

and use `redis-cli -p 6390 flushall` instead of `redis-cli flushall` below.

## Baseline on main 86fe4e40 (1 Nov)

- exits-never-blocked: 192 passed
- PAPER = LIVE: 86 passed

## Exits-never-blocked suite

    cd backend && redis-cli flushall && python -m pytest -q -p no:cacheprovider \
      tests/test_m55_s2_exits.py tests/test_contract_exits.py tests/test_def0_exits_and_gates.py \
      tests/test_worker_exits_without_deployment.py tests/test_reexit_guard.py tests/test_cx_group_exits.py \
      tests/test_exit_logic.py tests/test_m55_s6_entry_guards.py tests/test_z10c_phase0_lock.py \
      tests/test_m68_order_defaults.py

## PAPER = LIVE suite

    cd backend && redis-cli flushall && python -m pytest -q -p no:cacheprovider \
      tests/test_m55_s3_one_path.py tests/test_se7_live_multileg.py tests/test_n1_e1_execution_core.py \
      tests/test_fno1_chain_ticket.py

New order-path PRs add their own exit / PAPER = LIVE tests to these lists in the same PR.
