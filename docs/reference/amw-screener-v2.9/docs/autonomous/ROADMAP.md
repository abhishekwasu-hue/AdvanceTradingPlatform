# Roadmap (next 10 tasks, highest priority first)

| # | Task | Depends on | Acceptance criteria |
|---|---|---|---|
| 1 | Commit this session's changes and push to GitHub; confirm CI green | owner (git push) | CI Linux (sandbox + PostgreSQL) and Windows jobs pass |
| 2 | FYERS live verification (Q-007), then a shared licensed feed if a redistribution licence is obtained (Q-001) | owner FYERS account; vendor quote | one live login + full trading day: bars match the FYERS terminal, statuses correct; shared-feed adapter passes provider contract tests |
| 3 | Live verification with real keys: OpenAI, Anthropic, Telegram official + own bot | owner keys | one successful call each; usage rows show provider tokens; message received |
| 4 | ~~Durable job/schedule execution~~ — done in v2.5.0 (records + orphan recovery); optional automatic re-run remains | — | — |
| 5 | ~~Paper forward testing tied to signals~~ — done in v2.5.0 | — | — |
| 6 | ~~Signal → chart/report navigation~~ — done in v2.5.0 | — | — |
| 7 | ~~Report retention + quota~~ — done in v2.5.0 with assumed limits (owner to confirm Q-005) | owner | — |
| 8 | Historical index constituents for backtests (survivorship) | data source | backtests over a universe use point-in-time membership |
| 9 | Load test on production-like server (scan throughput, API p95) | server | documented latency/throughput; no capacity claim beyond the test |
| 10 | Independent security review of sandbox workers (JS realm, R deny-list) before public multi-tenant launch | — | review recorded; findings fixed or accepted |

Follow-ups from v2.4.0 (live data; the shared per-minute budget was done in v2.5.0): optional FYERS WebSocket tick source feeding `MinuteAggregator`; Dhan adapter on the same
collector interface; stream replay from a client-supplied cursor.

## After the continuation audit (2026-10-19)
1. Owner-dependent live verifications (FYERS Q-007, AI keys, Telegram) — the remaining "mock-only" integrations.
2. Measure API latency (p50/p95) for `/scan`, `/signals`, `/jobs/*` on PostgreSQL with realistic row counts; add
   indexes only where query plans show need.
3. Live-data profiling of a full Nifty 500 scan (network vs analysis split) before further CPU optimisation (A-006).
4. Owner decision on A-005 (keep immutable records vs. append a revised record when a stored signal upgrades).
