# Roadmap status (MASTER SPEC v1 + v1.1 / v1.2 / v1.3)

Working rules: docs/WORKING_RULES.md (multitasking and self-review are required). Updated with docs/WORK_LOG.md (at least every 2 hours while work runs). Merge only on the owner's "Merge".
Order: A -> C1+C4 -> D -> B (ADR -> build) -> E -> C2-C6 -> F -> G1-G2 -> H -> I -> G3-G6 -> J -> K.
Indian PAPER go-live on Hostinger comes before the global work (v1.2); crypto paper (CoinDCX spot) after it (v1.3).

Last update: 2026-10-11 00:31 IST.

| Part | What | Status | PR | % |
|---|---|---|---|---|
| A | Review fixes (13 items) | done, CI green, waiting for the owner's merge | #82 | 100 |
| Go-live | Hostinger KVM 2: bootstrap / deploy / rollback blocks, runbook, 8 GB budget | done, CI green, waiting for the owner's merge; the owner runs the blocks when the server IP exists | #84 | 100 |
| Go-live | H-1 Redis guard (lock loss fails closed, memory warning) + H-2 Cloudflare R2 off-site | done, CI green (stacked on #84) | #85 | 100 |
| C1 | HTF lookahead: closed bars only | done | #83 | 100 |
| C3 | Speed (indicator prefix cache), benchmark, CI guard | done; portfolio backtest still to come | #83 | 75 |
| C4 | Reproducibility fingerprint | done | #83 | 100 |
| C2 | Pluggable fill / slippage / latency / margin / pricing / settlement models | PR 1 done (ModelSet, fixed-pct slippage, touch fill, golden hashes); PR 2 next | #100 | 25 |
| C5 | Trial ledger, deflated Sharpe | after C2 | - | 0 |
| C6 | Distributed runs | after C5 | - | 0 |
| D | SEBI retail-algo: design + rule-set as data + COMPLIANCE_IN.md | done (design PR) | #87 | 100 |
| D2 | OPS throttle per client/exchange, exits first, 429 back-off (flag off) | done | #89 | 100 |
| D3 | Per-broker order-type policy for algo entries | done | #90 | 100 |
| D4 | Registered static egress IPs (model, weekly-change rule, LIVE gate off, readiness) + Settings card | done | #91, #92 | 100 |
| D5 | Daily broker login method per adapter + pre-open reminder | done | #93 | 100 |
| D1 | Per-broker algo tag format, generic vs registered algo id, algo_order audit rows | done (full suite 1339 passed) | #94 | 100 |
| D6 | Go-live checklist evidence (vendor, strategy class, AI, DPDP) | done | #95 | 100 |
| D7 | F&O ban period from lake MWPL/OI (flag off); FutEq client limit, expiry-day margin, lot as-of next | first slice done | #98 | 40 |
| B0 | ADR-0013 data lake, ADR-0016 provider seams, ADR-0017 global-first (provisional) | done (design PR) | #88 | 100 |
| B1 | Lake schema + as-of reads (hypertables only if Timescale is installable) | done; CI guard fix pushed after full suite | #96 | 100 |
| B2 | Ingest: candle builder (bar END), idempotent writer, broker backfill, vendor seam, tick writer (flag off) | done | #97 | 100 |
| B3 | Quality detectors (gap, spike, invalid, late, duplicate, mismatch) | done | #99 | 100 |
| B4 | Corporate-action adjusted view | done | #101 | 100 |
| B5 | history API with as_of / adjusted / quality | done | #102 | 100 |
| B6-B7 | backtests read the lake, retention/compression/metrics | next | - | 0 |
| E | Execution core and scale (ADR-0014) | not started | - | 0 |
| F | Portfolio risk | not started | - | 0 |
| G | Trade repo engine port (ADR-0015) | not started | - | 0 |
| H | Copilot v2 (ATP_COPILOT_SPEC): design note, review findings G1-G13 checked | done (design) | #103 | 100 |
| H-C1 | Safety: a server-side evidence, b AI rate limit, c output filter (en+mr), d grounding, e llm_calls hygiene, f prompt_version | a done; b, c built (suite run); d in progress | #103 | 50 |
| H-C2.. | Tool-calling agent core, evals, research loop, memory, ... (ADR-0019/0020 first) | after H-C1 | - | 0 |
| S0 | Screener v2: spec, design note, ADR-0021 engine, ADR-0022 notification service; addendum U1 planned | done (design) | #104 | 100 |
| U1-a/b | NSE universe: securities + symbol history; index catalogue + membership as-of + NSE sector classification | built, full suite next | - | 60 |
| S1, S3 | ScreenQL engine + scanner migration; Notification Service on the existing outbox | after H-C1 | - | 0 |
| I | Options analytics | not started | - | 0 |
| J | UX / real-time | not started | - | 0 |
| K | Community / enterprise | not started | - | 0 |
| v1.3 | Crypto adapters: CoinDCX (exists) -> Binance spot -> Bybit/KuCoin/OKX (perps/options only where allowed) -> CoinSwitch PRO/ZebPay; Kraken later; registry `fiu_registered` (India resident: FIU-registered + spot only); 30 % + 1 % TDS report seam | after the Hostinger PAPER go-live | - | 0 |
| v1.2 §2 | IBKR adapter: TWS (ib_async + Gateway, paper default) and Web API (stub) transports, jurisdiction engine, global master, venue costs | design PR (ADR-0018) first; build after go-live and crypto Phase 1 | - | 0 |

Owner decisions pending (provisional decisions taken, work continues): D-1..D-4 (#87, #94, #95), C2-1/C2-2 (#100), B-1, B-2 (#88), H-3..H-5 (#103), SC-1..SC-6 and U1-Q1/Q2 (#104). On the owner: R2 bucket +
token in the server .env; the server IP for the Hostinger blocks.
