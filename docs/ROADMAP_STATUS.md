# Roadmap status (MASTER SPEC v1 + v1.1 / v1.2 / v1.3)

## Phase 0 (owner's scope freeze - docs/specs/ATP_PHASE0_FREEZE.md; overrides the order below until Phase 0 ships)

Phase 0 = invite-only beta (300-500 users), PWA, PAPER only, no API orders from ATP. Progress: Z1, Z4, Z6 and Z9 merged; Z5 P1-c merged (P1-d in review); Z2 BT1 merged (BT1b in review); Z3, Z7a, Z7 and Z8 in review/progress; Z10, Z11 not started. Every PR waits for green CI and, for engine/data/security/migration work, an independent review; flags stay off.
Owner-side blockers: Hostinger server IP; R2 bucket/token in the host .env; Dhan partner application; company registration (needed before the partner emails in docs/specs/ATP_PARTNER_EMAIL_DRAFTS.md go out).

| Z | What | Status | PR | % |
|---|---|---|---|---|
| Z1 | PW5 proposal card + PW6 invite gate / feedback | merged | #162, #159 | 100 |
| Z2 | BT1 Tier A per-user broker feed (Upstox, Dhan), entitlement/isolation guard, login reminder, data-tier badges, BROKER_DATA_TERMS gating | BT1 merged (flag byob_data_tier off); BT1b (guard every live read, credential holder, staleness) in review: security review fixes in progress | #167 merged, #177 | 85 |
| Z3 | BT2/BT3: proposal -> Telegram/in-app/push -> Approve PAPER, per-user PAPER account, protective exits automatic, approval expiry/re-quote, ladder card | Z3-a approval-only entries + Z3-b PAPER account/ladder/Telegram: independent review and verification done (no safety blocker); last verification fixes in progress | #173, #174 | 85 |
| Z4 | HB0 R2 importer + HB1 daily 10-year backfill + nightly EOD jobs; owner intraday store tenant-scoped | merged (flags history_import / history_backfill off); owner intraday history comes from HB2 after Z11 (see note) | #164, #169 | 100 |
| Z5 | P1-c builder page (payoff, draggable strikes, metrics) + P1-d plan-before-trade ticket, risk-by-default caps / lockouts | P1-c merged (+ review follow-ups #180); P1-d (ticket, caps, daily loss lockout with exits allowed, strike breach, VIX gate with its own live reading) in review with the independent review fixed (flags `risk_by_default`, `vix_gate` off) | #170, #180 merged; #179 | 75 |
| Z6 | P1-e journal auto-capture, behaviour tags, discipline score, weekly review | merged (flag discipline_engine off) | #171, #172 | 100 |
| Z7a | Market zones port from Trade (demand/supply, order blocks, S/R, dynamic S/R, level memory; ATP lifecycle fresh -> tested -> flipped -> invalidated) | Z7a-1 detection merged (6528/6528 identical to Trade); Z7a-2 lifecycle/memory/store reviewed, review fixes pushed (job off the trading worker, all-daily defaults, settings-hash reads, retention, compressed golden fixtures; parity 5336/5336) - re-review next; Z7a-3 chart layer + ScreenQL zone functions next | #168 merged; #176 | 55 |
| Z7 | U6 BK1 + BK2 breakout screens + quality overlay; U8 EV1 + EV2 evidence charts | BK1 reviewed, fixes in progress (template history depth, alert flag gates, IST anchoring); BK2 built, aligning with BK1 fixes; EV1 + EV2 after Z7a-3 | #178, #181 | 35 |
| Z8 | U9 TX1 taxonomy + gallery chips; U7 LG1 (only if Z1-Z7 done) | TX1 merged (flag `screener_gallery` off): taxonomy data file + lint + CI guard (every pre-built screen filed), `GET /api/screener/taxonomy`, gallery rail / chips / matrix; breakouts register when #178 merges (OPEN_QUESTIONS TX1-6); LG1 next | #184 merged | 60 |
| Z9 | BYOK-V2 Vision audit on proposals (own key, caps) | merged (V1 #160, V2 #163) | #160, #163 | 100 |
| Z10 | Launch hardening (U3 subset G1/G2/G4/G5/G6/G7), load test 500 sessions, staged rollout flags, DPDP/terms/risk pages, support bot | design note `docs/design/Z10_LAUNCH_HARDENING.md` (Z10-a..f); Z10-a (G2 no-look-ahead harness + G1 golden / chart parity) in review as draft PR #187 | #187 | 10 |
| Z11 | Hostinger deploy bundle (owner-run command, rollback, R2 backup verified) | next | - | 0 |

Also merged: #183 fix - the tax report uses the platform charges table (india_costs) and a configurable F&O turnover method (STT rows from 1 Apr 2026 still marked verify, OPEN_QUESTIONS TX-1).

Data source note: the Trade-repo data collector was cancelled by the owner (2026-10-11), so HB0's R2 importer (#164) stays idle unless data arrives in that store. HB1 (NSE bhavcopy, run on the server) covers the daily history. The owner's intraday history comes from **HB2** (an intraday capture/backfill job on the server), kept in the plan right after Z11.


docs/TRADE_PORT_MAP.md maps Trade -> ATP (Phase 0 ports fold into Z3/Z5/Z10; see below). Parked until Z11 merges: BYOK-V3 (N5; work so far kept on branch claude/byok-v3-scan-daily), E/U4 auto modes, F, G, CH3-CH7 beyond evidence charts, BV vendor tier, Capacitor/Tauri, crypto, IBKR, K.
Not doing: the Trade-repo data collector (owner cancelled it 2026-10-11).

### After Phase 0 (N-list, docs/specs/ATP_PHASE0_FREEZE.md §3)

Starts as soon as Z11 merges, in this order. A Phase 0 bug, beta feedback or a security issue pre-empts N-work.

| N | What | Status |
|---|---|---|
| HB2 | Owner intraday history on the server (replaces the cancelled Trade-repo collector as the intraday source) | right after Z11 |
| N1 | E execution core (ADR-0014) -> U4 X1-X2 -> F portfolio risk. The proposal engine reuses the Trade level-bot confirmation rules (trend first -> area -> handover study -> dominant commitment candle on higher volume -> Vision -> approval) once the Trade replay report is in; see docs/TRADE_PORT_MAP.md | after Z11 |
| N2 | P1-f/g/h: progression ladder, decision tools, context panel, manual-trading workspace polish, education pages | after Z11 |
| N2-J | **Journal module (owner spec, after Phase 0)**: `docs/specs/ATP_JOURNAL_MODULE_SPEC.md` (v1 + v1.1). Extends the Z6 journal package (`backend/app/journal`, #171/#172): broker API sync (read-only) and file import, fills -> positions -> trade groups, charges on `india_costs`, auto charts, rulesets and checklist, AI text review and BYOK vision review, trade plan and alert-only guardrails, Telegram/email reports, analytics, plans on the existing Razorpay billing, tax, mentor mode. English UI only for now. Plan: `docs/journal/PLAN.md` (PRs J1-a..J8-c); questions JM-1..JM-34 in `docs/journal/OPEN_QUESTIONS.md` | plan done (milestone 0); build after Z11, next to N2 |
| N3-N9 | Remaining screener, charting CH3-CH7, Copilot H-C4+ / BYOK-V3, G trade engine port, backtest C2-C6 / I, Phase 1 items behind flags, crypto / IBKR / K | after Z11, in that order |

### Trade port map

docs/TRADE_PORT_MAP.md maps every Trade repo module (Trade@eafc872) to ATP, with status and parity tests.
Each port PR updates its row in the map and these counts.

| Status | Rows (of 192) |
|---|---|
| ported | 11 |
| partial | 18 |
| partial (unverified) | 9 |
| in progress (Z7a) | 6 |
| missing | 49 (25 are G rows that wait for the owner's "stable" mark) |
| not needed | 99 |

Phase 0 ports (Z3 / Z5 / Z10): order safety (partial), charges golden check (partial), strike breach model (ported, Z5 P1-d),
India VIX gate (ported, Z5 P1-d), PAPER journal / Telegram commands (partial), PAPER exit engine (partial), expiry rule
(partial: ported, not wired).

Working rules: docs/WORKING_RULES.md (multitasking and self-review are required). Updated with docs/WORK_LOG.md (at least every 2 hours while work runs). Own PRs are merged when CI is green and review passes (owner's Phase 0 rule); flags stay off.
Order: A -> C1+C4 -> D -> B (ADR -> build) -> E -> C2-C6 -> F -> G1-G2 -> H -> I -> G3-G6 -> J -> K.
Indian PAPER go-live on Hostinger comes before the global work (v1.2); crypto paper (CoinDCX spot) after it (v1.3).

Last update: 2026-10-11 20:45 IST (refreshed after every merge).

## Parts and status

Merge train (owner's MERGE_NOW): finished 09:52 IST. PRs #82-#155 are all merged - 72 squash merges to main, plus #106 merged into its own chain (its content reached main with #105/#155). Logs of the train branches land with this docs PR (#154). Main after the last merge (2cb833d): frontend tsc / vitest 167 / build / bundle budgets / English-only check green. Nothing deployed, no flag on, no LIVE, no secrets touched.

| Part | What | Status | PR | % |
|---|---|---|---|---|
| A | Review fixes (13 items) | merged | #82 | 100 |
| Go-live | Hostinger KVM 2: bootstrap / deploy / rollback blocks, runbook, 8 GB budget | merged; the owner runs the blocks when the server IP exists | #84 | 100 |
| Go-live | H-1 Redis guard (lock loss fails closed, memory warning) + H-2 Cloudflare R2 off-site | merged | #85 | 100 |
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
| S1, S3, S4 | ScreenQL engine + scanner migration; Notification Service on the existing outbox; bar-close alert engine | S1a-S1d done (S1d API: closed bars only); S3a rules/events/throttle, S3b-1 webhook schema + dead letters, S3b-2 Telegram link + email unsubscribe done (suite 1369 passed); S3c UI, S4a bar-close engine, S4b-1 cycle cache, S4b-2 intrabar alerts (flag `screener_intrabar`, off; suite 1384 passed) done; S5-A1 price action series, S5-A2 `ReversalAt`, S5-A3 `RealBreak`, S5-A4 swing zone strength/distance (each with an independent review pass and follow-up; history per swing degree); U5 D1 screener page (tokens, funnel canvas, indicator block, result table, per-stage survivors); U5 D2 block forms, ANY / NOT groups, live counts, why-matched (#150; bars-only run path, SC-18); next U5 D3 and S5-C | #122-#125, #127-#129, #131, #132, #134, #138, #140, #143-#146, #150 | 90 |
| I | Options analytics | not started | - | 0 |
| CH0 | Advanced charting: spec, design note, ADR-0023 ChartEngine | done (design) | #107 | 100 |
| CH1.. | ChartEngine interface, drawings storage, v5 upgrade, layers, option-contract charts | CH1a drawings storage, CH1b ChartEngine + B-lite adapter, CH2a lightweight-charts v5 (render parity), CH2b primitives drawing core (all 12 drawing/1 kinds; second-pass ray fix) done; CH2c-1 tool controller (place, drag, undo/redo, conflicts) + CH2c-2 wiring and toolbar on ProChart (draft PRs); CH3 layers next | #126, #130, #133, #136, #139, #141 | 60 |
| P1 | Profitability mission, manual trading, Options Strategy Builder (P1 spec) | P1-a core port (#148); P1-b model - T+0 / 0DTE curves, exact PoP and extremes, 38 templates, any broker's chain (#149); P1-c builder API + page with payoff canvas, draggable strikes, metrics (PRs next); P1-d basket / order ticket next | #148, #149, P1-c PRs | 8 |
| OI | OI banner (Trade port): O1 oi_regime + golden fixtures, O2 collector/tables/history API, O3 banner frontend, O4a alerts, O4b Telegram buttons/digest/settings UI, O5 opt-in deployment gates | all built and merged | #109, #111-#114, #116 | 100 |
| PW1 | PWA shell: manifest + icons, service worker app-shell cache (never market data), offline page, install + update prompts | done, CI green, draft | #156 | 100 |
| PW2 | Mobile layout: bottom navigation, safe areas, 44 px targets, keyboard-aware fields, chart tools sheet, Screener bar above the nav, phone visual regression (360 / 390, dark / light) | built on its branch; PR after PW1 merges | - | 90 |
| PW3 | Web push everywhere: explain-first permission, iOS install-first, categories, deep links, delivery log | done, draft (stacked on #156) | #157 | 100 |
| PW4 | Telegram <-> app: Open in app on alerts and proposals; live stream (decisions on both sides within 2 s) | done, draft (stacked on #157) | #158 | 100 |
| PW5 | Phase 0 proposal card (Approve PAPER / Reject / Snooze, broker-app deep link) | next | - | 0 |
| PW6 | Beta cohort gate (invite codes, cap 500), install/update metrics, Send feedback (flag beta_cohort_gate off) | in progress | - | 50 |
| BYOK-V1 | Vision with the user's own AI key: per-user encrypted keys, key test, caps, settings (flag vision_byok off) | done, draft | #160 | 100 |
| J | UX / real-time | not started | - | 0 |
| K | Community / enterprise | not started | - | 0 |
| v1.3 | Crypto adapters: CoinDCX (exists) -> Binance spot -> Bybit/KuCoin/OKX (perps/options only where allowed) -> CoinSwitch PRO/ZebPay; Kraken later; registry `fiu_registered` (India resident: FIU-registered + spot only); 30 % + 1 % TDS report seam | after the Hostinger PAPER go-live | - | 0 |
| v1.2 §2 | IBKR adapter: TWS (ib_async + Gateway, paper default) and Web API (stub) transports, jurisdiction engine, global master, venue costs | design PR I1 done (ADR-0018 transports, BROKERS_IBKR runbook, IB-1..IB-5); build I2-I7 after go-live and crypto Phase 1 | #137 | 10 |

Owner decisions pending (provisional decisions taken, work continues): D-1..D-4 (#87, #94, #95), C2-1/C2-2 (#100), B-1, B-2 (#88), H-3..H-5 (#103), SC-1..SC-6 and U1-Q1/Q2 (#104), SC-7..SC-11 (#125-#138), H-6..H-9 (#121, #135), CH-1..CH-6 (#107, #136), IB-1..IB-5 and CR-1 (#137). On the owner: R2 bucket +
token in the server .env; the server IP for the Hostinger blocks.
