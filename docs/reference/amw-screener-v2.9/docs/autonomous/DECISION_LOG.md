# Decision log

| ID | Problem | Decision | Rationale | Owner approval |
|---|---|---|---|---|
| D-001 | Repository policy on automated commits | Do **not** commit in this session; leave changes in the working tree | The master prompt forbids automatic commits unless authorised; the owner pushes manually | No (reversible) |
| D-002 | Making signals tenant-scoped without breaking existing DBs | Additive columns (`user_id`, `canonical_key`) + per-owner scoped primary key; legacy rows stay ownerless | No destructive migration; works on SQLite and PostgreSQL | No |
| D-003 | Telegram delivery model | Official AMW bot (operator token, users link via `/start <code>`) **and** user-owned bot (encrypted token); no shared chat | Required by spec; removes cross-tenant delivery | No |
| D-004 | Email delivery | Operator SMTP server, per-user destination address | Minimal change that removes the shared mailbox | No |
| D-005 | Static chart rendering engine | matplotlib (headless Agg) instead of Plotly+kaleido | kaleido needs a browser runtime; matplotlib is deterministic and already a common dependency | No |
| D-006 | PDF library | reportlab 4.4.4 (BSD) | mature, pure Python, Windows-friendly | No |
| D-007 | Chart data for reports | recompute from current validated data truncated at the signal's data bar, compare snapshot fingerprint, label mismatch | full OHLCV snapshots are not stored (storage cost); fingerprint makes revisions visible | Yes, if exact replay is required (store snapshots) |
| D-008 | Plan limits | config-driven defaults, enforced in backend | prices/limits are commercial decisions (Q-002) | Yes |
| D-009 | Backtest small samples | label `INSUFFICIENT EVIDENCE` under 30 trades, keep the numbers | hiding numbers would conceal what happened; labelling prevents over-reading | No |
| D-010 | Scheduler | in-process thread in the API (and desktop) process | single-server deployment; no new infrastructure; documented restart limitation | No |
| D-011 | Shared vs per-user broker data | Per-user FYERS connection; bars, status and stream scoped to the owner | FYERS API terms prohibit redistribution; a shared feed needs a vendor licence (Q-001) | Yes (owner chose FYERS) |
| D-012 | Collector placement with two processes (dashboard + API) | Database lease (one collector per user), heartbeat status, Stop via lease release, WebSocket tails `minute_bars` | The database is the only shared state; avoids double polling (account-block risk) without adding Redis/a broker | No |
| D-013 | Polling vs FYERS WebSocket ticks | Poll `data/history` once per minute for completed candles | Uses FYERS' own candles (no local tick aggregation errors), catches up missed minutes, fits the rate budget for ≤ 100 symbols; a tick aggregator (`MinuteAggregator`) exists for a future WebSocket source | No |
| D-014 | FYERS session lifetime | Treat the token as valid until 06:00 IST next day; daily login | Refresh tokens need the user's PIN and were not assumed; conservative expiry avoids using a stale token | No (verify live, Q-007) |
| D-015 | Scan behaviour on an expired broker session | Abort the scan with an explicit error; status endpoint and Screener show the fallback | Silent "no data" for every symbol would look like a market result | No |
| D-016 | Job durability without new infrastructure | Record jobs in `job_runs` with heartbeat; mark orphans FAILED on start-up; no automatic re-run | Re-running a scan after a restart could repeat alerts/Telegram messages; a visible FAILED with a reason is safer | No |
| D-017 | Forward-test fill model | Next-bar open entry, stop-first on ambiguous bars, gap fills at the open, fixed expiry | Conservative and reproducible; mirrors the backtest assumptions | No |
| D-018 | Cross-process FYERS minute budget | Database fixed-window counter (`rate_buckets`) | Removes the last double-budget gap without Redis | No |
| D-019 | Concurrent duplicate signal inserts | `INSERT … ON CONFLICT DO NOTHING` per dialect instead of savepoints | pysqlite savepoints need driver workarounds; ON CONFLICT is atomic on SQLite and PostgreSQL | No |
| D-020 | Forming weekly bar in backtests | Drop it (match the live scanner) rather than resample differently | Live/backtest consistency is the requirement; Friday-holiday weeks are dropped one bar late (conservative) | No |
| D-021 | Scanner storage | Scanner = Strategy Lab strategy + `spec["scanner"]` envelope | reuses immutable versions, tenancy, sandbox, scans, backtests; no duplicate engine | Yes (Q-008) |
| D-022 | NL builder | deterministic registry-bound parser; unmatched clauses reported, never substituted | prompt forbids invented/substituted logic; works without AI keys | Yes (Q-011) |
| D-023 | New candle patterns | new names only; built-ins filter by their own lists | guarantees unchanged built-in results (fingerprint check) | No |
| D-024 | Replay data | current provider history cut at as_of + forming-bar drop + quality at as_of | no point-in-time vendor snapshots exist; limitation shown in every replay | No |
| D-025 | System notifications | separate `notification_events` table (schema v8) with DB-unique dedup | alert_events is rule-bound; system events have no rule | No |
| D-026 | API threads | keep default 40; add measured knob | a lower default risks head-of-line blocking by slow endpoints | Yes (Q-014) |
| D-027 | Duplicate scheduled runs across processes | atomic compare-and-set claim per slot instead of a process-wide scheduler lease | keeps both processes useful (no single scheduler owner), no new infrastructure | No |
| D-028 | Provider failover | whole-frame per symbol, refuse on discrepancy, reject mixed data kinds, opt-in | prompt forbids merging incompatible candles and presenting delayed data as live | No |
| D-029 | Sharing | copy, not live link | a live shared object would need workspace permissions and change-propagation rules (owner decision) | Yes (Q-012) |
| D-030 | Q-014 path | Multi-process workers knob + opt-in async endpoints; defaults unchanged | measured 14x throughput without breaking API contracts; defaults remain an owner decision | Yes (Q-014) |
| D-031 | Instrument master | built only from official lists loaded by the app; first valid ISIN kept; names resolved only when unique | no invented reference data; ambiguity surfaced to the user | No |
| D-032 | A-005 (owner-approved) | Immutable original signal record (DB trigger) + append-only lifecycle events; compare-and-set transitions | no revised duplicates; events traceable; retries idempotent | **Approved** |
| D-033 | Q-014 (owner-approved) | 4 API workers in production compose, desktop 1; sync contracts unchanged; async opt-in with bounded retries | measured on SQLite and PostgreSQL; diminishing returns beyond 4 on 4 vCPU; not a capacity guarantee | **Approved** |
| D-034 | Q-010 (owner-approved) | Elliott/Harmonic excluded; candidate-only extension point, empty registry | subjective patterns must not influence scores | **Approved** |
| D-035 | Q-012 (owner-approved) | user-to-user copy only; no workspaces/marketplace | — | **Approved** |
| D-036 | Q-002 (owner-approved) | plan structure kept; values provisional; no prices; no billing activation | commercial terms need a business decision | **Approved** |
| D-037 | Q-005 (owner-approved) | 7/90/365/365 days provisional; legal hold; recent/active protection; audited cleanup | report retention distinct from backups | **Approved** |

