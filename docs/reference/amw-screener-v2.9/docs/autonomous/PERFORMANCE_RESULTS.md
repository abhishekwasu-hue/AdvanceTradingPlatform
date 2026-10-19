# Performance results (measured)

Benchmark: `run_scan` on 40 synthetic symbols with the deterministic fixture provider (no network), all enabled
strategies, 3 runs per variant, same machine, same process conditions. This measures **analysis cost only**; live scans
are usually dominated by provider latency (see docs/TEST_REPORT.md for live Yahoo timings). No concurrency/capacity
claim is made.

| Variant | Runs (s) | Median (s) | Signals | Result fingerprint (sha256 of key, score, status, snapshot) |
|---|---|---|---|---|
| Before (v2.5.0) | 6.52, 6.51, 7.15 | **6.52** | 232 | a95857642897f5c5 |
| After snapshot cache + NumPy OHLC check | 6.03, 5.93, 6.12 | 6.03 | 232 | a95857642897f5c5 |
| After single true range + one-concat indicators | 4.92, 4.96, 5.58 | **4.96** (−24 %) | 232 | a95857642897f5c5 |

Identical fingerprints show the optimisation did not change any signal, score, status or data snapshot. Indicator
equivalence was additionally checked column by column against the previous implementation on 1D, 1W, 1H, 15m, 5m.

Profile before (20 symbols, cProfile): analyse_symbol 3.5 s of 5.0 s; add_indicators 1.5 s; clean_and_validate 1.5 s
(126 calls); data_snapshot_hash 0.44 s (115 calls, one per signal); strategies 0.58 s.
Not measured: API endpoint latency under load, PostgreSQL query plans at scale, full-universe live scan.

## v2.6.0 measurements (same machine, later session; absolute times vary between sessions)

### API latency — real `uvicorn`, SQLite, 5,000 stored signals, 600 requests, 20 concurrent clients (4 vCPU)
| Endpoint | p50 ms | p95 ms | p99 ms | req/s | errors |
|---|---|---|---|---|---|
| /health/live | 51.9 | 155.7 | 224.6 | 291.7 | 0 |
| /api/v1/meta | 102.6 | 220.7 | 1705.3 | 123.9 | 0 |
| /api/v1/signals?limit=100 | 1074.8 | 1609.0 | 1821.4 | 20.3 | 0 |
| /api/v1/jobs | 74.9 | 183.9 | 255.2 | 232.6 | 0 |
| /api/v1/forward | 105.2 | 147.0 | 170.7 | 184.6 | 0 |
API process RSS after the run: 227 MB.

### P-001 / P-002 investigation (signal list, limit 100)
| Variant | conc 1 req/s | conc 20 p50 ms | conc 20 p95 ms | conc 20 req/s |
|---|---|---|---|---|
| before index (default 40 threads) | — | 1074.8 | 1609.0 | 20.3 |
| index + no payload (40 threads) | 63–68 | 1261–1317 | 1500–1795 | 14.7–15.3 |
| 8 threads | 69.8 | 1123.7 | 1279.0 | 17.8 |
| 4 threads | 65.3 | 737.4 | 843.8 | 26.7 |
| 4 threads + switch interval 0.5 ms | 62.6 | 590.5 | 687.0 | 33.7 |
| 2 threads | 70.9 | 300.3 | 346.0 | 64.8 |
| 1 thread | 74.6 | 279.6 | 319.3 | 71.8 |
The database query fell from 14 ms to 1.9 ms with the index, yet throughput under concurrency did not improve: the
bottleneck is thread contention, not SQL. Larger connection pools (tested 20 + 10) changed nothing (15.1 req/s).

### Backtest cost of point-in-time HTF context (A-007), fixture data
| Backtest | before | after | trades |
|---|---|---|---|
| breakout 1D, 400 bars | 3.04 s | 4.47 s | 3 → 3 |

### Scan with v2 analysis modules (40 synthetic symbols)
Built-in scan result fingerprint unchanged (a95857642897f5c5). A/B with the new modules disabled vs enabled in the same
session: medians 6.20 / 6.59 s (off) vs 7.13 / 6.65 s (on) — within the run-to-run spread. Supertrend optimised from
3.1 ms to 0.82 ms per 400-bar frame. Not measured: load test with PostgreSQL, multi-process deployment, full-universe
live scan.

## v2.8.0 — multi-process API (P-002 resolution path)
Real `uvicorn api_server:app --workers N`, SQLite, 5,000 signals, 400 requests per endpoint, 20 concurrent clients,
4 vCPU, scheduler and feed auto-resume enabled in every worker.
| Endpoint | workers=1 p50 / p95 / p99 ms | req/s | workers=4 p50 / p95 / p99 ms | req/s | errors |
|---|---|---|---|---|---|
| /api/v1/signals?limit=100 | 1354.7 / 1928.1 / 2356.8 | 13.9 | 84.1 / 183.4 / 208.3 | **196.6** | 0 / 0 |
| /api/v1/jobs | 68.8 / 132.2 / 175.9 | 268.1 | 52.4 / 81.7 / 103.3 | 350.2 | 0 / 0 |
| /health/live | 48.5 / 156.3 / 235.0 | 291.8 | 50.1 / 76.5 / 107.1 | 374.7 | 0 / 0 |
No errors or tracebacks in the 4-worker log. `python api_server.py` with `AMW_API_WORKERS=2` verified to start a
parent + 2 workers and report ready. Not measured: PostgreSQL under multi-process load; more than 4 workers.

## v2.9.0 — PostgreSQL 16, uvicorn workers 1/2/4/8 (Q-014 representative load)
4 vCPU, 5,000 signals, 20 concurrent clients; reads 400 requests, writes (paper journal insert) 200 requests; scheduler
and feed auto-resume enabled in every worker; 0 errors and 0 tracebacks in all runs.
| Workers | signals p50 / p95 ms | signals req/s | jobs req/s | journal POST p50 / p95 ms | journal req/s | workers RSS |
|---|---|---|---|---|---|---|
| 1 | 272.4 / 355.3 | 71.8 | 233.8 | 123.7 / 173.1 | 156.6 | n/a (single process) |
| 2 | 129.1 / 195.5 | 144.8 | 272.0 | 79.7 / 166.6 | 223.9 | 369 MB |
| 4 | 91.0 / 193.4 | **179.0** | 309.9 | 68.3 / 103.8 | **259.8** | 715 MB |
| 8 | 80.0 / 139.1 | 221.3 | 316.4 | 69.5 / 100.3 | 261.3 | 1414 MB |
Reading: on PostgreSQL a single worker suffers far less thread contention than on SQLite (71.8 vs 13.9 req/s);
4 workers roughly 2.5x reads / 1.7x writes; 8 workers add ~24 % reads, ~0 % writes for twice the memory. Initial
production value 4 (D-033) is supported for a 4-vCPU host; this is not a capacity guarantee (no long-duration test,
no scans running concurrently, client and server on the same host).

