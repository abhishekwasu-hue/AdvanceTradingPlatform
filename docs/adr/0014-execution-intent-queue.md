# ADR-0014: Execution core - one intent queue in Postgres, drained by the trading worker, one routing policy

**Date:** 2026-10-12 · **Status:** provisional (N1 E1; the owner may change it) · **Part:** E (N1)
**Design note:** `docs/design/N1_EXECUTION_CORE.md`

## Context
ADR-0014 was reserved for part E's "event bus / queue". N1 (docs/specs/ATP_PHASE0_FREEZE.md §3) starts with the E
execution core, then U4 X1-X2 (Notify / One-click / Auto PAPER for scans), then F portfolio risk.

Today every automatic entry has its own path to the order pipeline or to a Z3 proposal:
- the trading worker for deployments;
- the TradingView webhook;
- the public API `POST /signals`;
- the console's paper-execute;
- the PAPER ticket.

Each path asks `approval.required` itself, and the Z3 census test lists every one of them. Scans (U4) would add more
producers, and some of them are API routes. Three rules hold all the time:
- Phase 0 / N1 places no broker API order (Z10-c: the `phase0_paper_only` lock and the static order-path guard);
- the AI never places an order (ADR-0006);
- exits are never blocked (ADR-0004).

## Options
1. Each new producer calls `approval.propose_entry` (or a pipeline) directly, as today.
2. A message broker (Redis Streams, NATS, Kafka) between producers and executors.
3. A Postgres table as the queue. Producers write rows; the trading worker claims them with a conditional UPDATE and
   routes each one through one pure policy.

## Decision (provisional)
**Option 3.**
- **Queue.** `execution_intents`: one row per intent. It is idempotent per organisation, person and key, and it holds the
  owner, the source, the requested mode, the signal, the evidence, the route, the status and the reason.
- **Producers only write.** `intents.submit` never dispatches. So an API route that queues an intent cannot reach an
  order pipeline through the core.
- **One consumer.** The trading worker (ADR-0010: one loop, Redis lock) runs `dispatcher.drain` every cycle, market
  open or not. A claim is a conditional UPDATE (`status = 'QUEUED'`), so two replicas never take the same row. A
  claim whose worker was interrupted is queued again after a time limit, and every route is idempotent on
  `intent:<id>`. After `EXECUTION_CORE_MAX_ATTEMPTS` claims the intent fails.
- **One policy.** `policy.decide` routes NOTIFY, ONE_CLICK, AUTO_PAPER and AUTO_LIVE to NOTIFY, PROPOSE, PAPER or
  REFUSE:
  - AUTO_LIVE is always refused in N1;
  - AUTO_PAPER becomes a proposal under approval-only mode, and also until U4 X2 adds its own switch.
- **No broker in the core.** Nothing in `app/execution/core` holds, builds or is given a broker. A test walks the Z10-c
  call graph and fails if any function there can reach a broker order method.
- **Flag.** `execution_core`, off by default.

## Consequences
- No new infrastructure: it is the same Postgres, and the same worker as the alert outbox (ADR-0022) and the Vision
  queue.
- Latency is one worker cycle (60 s by default). This is fine for bar-close scans. A tick-driven path would need a
  real bus later; the `submit` / `claim` interface is the seam for it.
- Every automatic entry leaves a row that says what was decided and why (REFUSED / EXPIRED included). This is the
  base for the X1 UI and for the F risk checks.
- The existing producers (webhook, public API, deployments) keep their paths for now. Moving them onto the queue is a
  later, separate change, each with its own review against the Z3 census.
