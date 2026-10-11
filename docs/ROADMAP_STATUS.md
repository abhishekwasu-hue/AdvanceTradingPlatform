# Roadmap status (MASTER SPEC v1 + v1.1 / v1.2 / v1.3)

Working rules: docs/WORKING_RULES.md (multitasking and self-review are required). Updated with docs/WORK_LOG.md (at least every 2 hours while work runs). Merge only on the owner's "Merge".
Order: A -> C1+C4 -> D -> B (ADR -> build) -> E -> C2-C6 -> F -> G1-G2 -> H -> I -> G3-G6 -> J -> K.
Indian PAPER go-live on Hostinger comes before the global work (v1.2); crypto paper (CoinDCX spot) after it (v1.3).

Last update: 2026-10-11 06:19 IST.

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
| H-C1 | Safety: a server-side evidence, b AI rate limit, c output filter (en+mr), d grounding, e llm_calls hygiene, f prompt_version | a-c done (#103); d-f done (full suite 1364 passed) | #103, #108 | 100 |
| H-C2.. | Tool-calling agent core, evals, research loop, memory, ... | ADR-0019/0020 (provisional); H-C2a core; H-C2b-1 proposal tools + injection guard; H-C2b-2 JSON answer contract + OpenAI tools; H-C2b-3 market/research tools; H-C10a golden sets + CI gate; H-C3a+b research loop (ledger, deflated report, loop + API behind `ai_research`, off; second-pass fixes: OOS warm-up, flat-draft verdict, PBO window, holdout trim) (each full suite green, flags off); H-C3c-1 studies as jobs (queue table, research worker under compose profile `research`, heartbeat, interrupted state) + H-C3c-2 Strategy Lab research panel (suite 1422 passed); H-C4 next | #115, #117-#121, #135, #142 | 70 |
| S0 | Screener v2: spec, design note, ADR-0021 engine, ADR-0022 notification service; addendum U1 planned | done (design) | #104 | 100 |
| U1-a/b/c | NSE universe: securities + symbol history; index catalogue + membership as-of + NSE sector classification; index EOD, F&O lots, ban list | a-c done (suite 1390 passed, Postgres migration checked); d/e after S1 | #105, #106, #110 | 75 |
| S1, S3, S4 | ScreenQL engine + scanner migration; Notification Service on the existing outbox; bar-close alert engine | S1a-S1d done (S1d API: closed bars only); S3a rules/events/throttle, S3b-1 webhook schema + dead letters, S3b-2 Telegram link + email unsubscribe done (suite 1369 passed); S3c UI, S4a bar-close engine, S4b-1 cycle cache, S4b-2 intrabar alerts (flag `screener_intrabar`, off; suite 1384 passed) done; S5-A1 price action series, S5-A2 `ReversalAt`, S5-A3 `RealBreak`, S5-A4 swing zone strength/distance (each with an independent review pass and follow-up; history per swing degree); U5 D1 screener page (tokens, funnel canvas, indicator block, result table, per-stage survivors); next U5 D2 and S5-C | #122-#125, #127-#129, #131, #132, #134, #138, #140, #143-#146 | 90 |
| I | Options analytics | not started | - | 0 |
| CH0 | Advanced charting: spec, design note, ADR-0023 ChartEngine | done (design) | #107 | 100 |
| CH1.. | ChartEngine interface, drawings storage, v5 upgrade, layers, option-contract charts | CH1a drawings storage, CH1b ChartEngine + B-lite adapter, CH2a lightweight-charts v5 (render parity), CH2b primitives drawing core (all 12 drawing/1 kinds; second-pass ray fix) done; CH2c-1 tool controller (place, drag, undo/redo, conflicts) + CH2c-2 wiring and toolbar on ProChart (draft PRs); CH3 layers next | #126, #130, #133, #136, #139, #141 | 60 |
| P1 | Profitability mission, manual trading, Options Strategy Builder (P1 spec) | P1-a builder core ported from the Trade repo (golden parity 1,451 cases, mutations 13/13; ADR-0025 proposed); P1-b T+0 / IV / PoP / templates next | P1-a PR | 8 |
| OI | OI banner (Trade port): O1 oi_regime + golden fixtures, O2 collector/tables/history API, O3 banner frontend, O4a alerts, O4b Telegram buttons/digest/settings UI, O5 opt-in deployment gates | all built; each suite green; waiting for the owner's merge | #109, #111-#114, #116 | 100 |
| J | UX / real-time | not started | - | 0 |
| K | Community / enterprise | not started | - | 0 |
| v1.3 | Crypto adapters: CoinDCX (exists) -> Binance spot -> Bybit/KuCoin/OKX (perps/options only where allowed) -> CoinSwitch PRO/ZebPay; Kraken later; registry `fiu_registered` (India resident: FIU-registered + spot only); 30 % + 1 % TDS report seam | after the Hostinger PAPER go-live | - | 0 |
| v1.2 §2 | IBKR adapter: TWS (ib_async + Gateway, paper default) and Web API (stub) transports, jurisdiction engine, global master, venue costs | design PR I1 done (ADR-0018 transports, BROKERS_IBKR runbook, IB-1..IB-5); build I2-I7 after go-live and crypto Phase 1 | #137 | 10 |

Owner decisions pending (provisional decisions taken, work continues): D-1..D-4 (#87, #94, #95), C2-1/C2-2 (#100), B-1, B-2 (#88), H-3..H-5 (#103), SC-1..SC-6 and U1-Q1/Q2 (#104), SC-7..SC-11 (#125-#138), H-6..H-9 (#121, #135), CH-1..CH-6 (#107, #136), IB-1..IB-5 and CR-1 (#137). On the owner: R2 bucket +
token in the server .env; the server IP for the Hostinger blocks.
