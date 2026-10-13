# Roadmap status (MASTER SPEC v1 + v1.1 / v1.2 / v1.3)

## Phase 0 (owner's scope freeze - docs/specs/ATP_PHASE0_FREEZE.md; overrides the order below until Phase 0 ships)

Phase 0 = invite-only beta (300-500 users), PWA, PAPER only, no API orders from ATP. Code: Z1-Z11 all merged (12 Oct); every new feature flag is off by default. The N-list is running (owner, 12 Oct): lane A N1, lane B N2 + N3, lane C N4 + fixes, lane D exit criteria / load test / docs.

### Phase 0 exit criteria

Results from the CI staging stack (PR #222, draft). The Hostinger re-runs wait for the server.

| Criterion | Result | Note |
|---|---|---|
| End-to-end acceptance flow, two test users (Upstox, Dhan) | **16/16** on the CI stack | Hostinger run pending the server |
| No API order path reachable in Phase 0 mode | **PASS** in CI | Z10-c static order-path guard + `phase0_paper_only` lock; Phase 0 deployment defaults (`live_trading` off, `phase0_paper_only` on at first boot) merged in #204 |
| Stale data => alerts suppressed | **PASS** | Z10-b guard, end-to-end on the CI stack |
| Load test, 500 sessions | **FAIL**: p95 2.9-4.9 s vs the 0.5 s target | perf work on `claude/perf-500` (with `claude/ltp-shared-budget`); the owner re-runs it on Hostinger with one command |
| Rollback rehearsed | **12/12** on the CI stack | Hostinger rehearsal pending the server |

### Owner blockers (only these four)

1. Hostinger server IP.
2. R2 bucket / token in the host `.env`.
3. Dhan partner application.
4. Company registration (needed before the partner emails in docs/specs/ATP_PARTNER_EMAIL_DRAFTS.md go out).

### N-list: every slice and its state (13 Oct, 02:30 UTC)

States: **merged** (on `main`); **PR open**; **review: MERGE / FIX** (the independent review's verdict on the branch
head named); **re-review: pass / fail / in progress** (a re-check of review fixes; every former "re-review: check" row now
says which); **awaiting review**; **parked** (with the reason); **not started** (no branch). Every flag named is off by
default. Owner rule (13 Oct): no new N-slice starts until fewer than 10 branches await review; lane D works only on the
load test; merges go in dependency order (BK3 -> BK4 -> BK5; LG2 -> LG3 -> LG4a; S6a -> S6b -> S7a -> S8 -> S9;
TX2 -> TX3 -> TX4; N2-4 ... N2-11; CH4a -> CH4b; CH5-b; EV3 / EV4) with at most 10 open PRs.

| N | Slice | Branch | PR | State |
|---|---|---|---|---|
| N1 | E1 execution core: intent queue + policy routing (flag `execution_core`) | `claude/n1-e1` | #206 | merged |
| N1 | L1 Trade level-gate port, golden parity | `claude/n1-l1` | #210 | merged |
| N1 | L2 Trade Daily trend fold port, golden parity | `claude/n1-l2` | #213 | merged |
| N1 | X1 scan Then: Notify / One-click through the level gate | `claude/n1-x1` | #214 | merged |
| N1 | X1b Then panel, read APIs, take-over guard (includes U5 D4) | `claude/n1-x1b` | #231 | merged |
| N1 | X2 Auto PAPER from armed scan templates (flag `scan_auto_paper`) | `claude/n1-x2` | #239 | PR open; re-review: pass (MERGE at d551cb5) |
| N1 | F1-F3 portfolio risk model, entry-only caps, cards | `claude/n1-f` | #219 | merged |
| N1 | E2 hardening: (a) CLOSING pre-claim (flag `exit_preclaim`), (b) `GET /api/execution/intents`, (c) venue contract suite | - | - | not started: no branch; next for lane A once the review backlog is under 10 (docs/design/N1_EXECUTION_CORE.md E2) |
| N2 | N2-1 ladder evidence | `claude/n2-p1f` | #203 | merged |
| N2 | N2-2 experience levels | `claude/n2-2` | #207 | merged |
| N2 | N2-3 decision tools I | `claude/n2-3` | #211 | merged |
| N2 | N2-4 decision tools II | `claude/n2-4` | - | re-review: in progress (main carried up after #211) |
| N2 | N2-5 market context panel | `claude/n2-5` | - | re-review: in progress |
| N2 | N2-6 positions v2 | `claude/n2-6` | - | re-review: in progress |
| N2 | N2-7 builder adjustments | `claude/n2-7` | - | re-review: in progress |
| N2 | N2-8 builder compare (flag `builder_compare`) | `claude/n2-8` | - | awaiting review |
| N2 | N2-9 trade workspace (flag `trade_workspace`) | `claude/n2-9` | - | awaiting review |
| N2 | N2-10 education pages (flag `education_pages`) | `claude/n2-10` | - | awaiting review |
| N2 | N2-11 learn from your journal (flag `journal_lessons`) | `claude/n2-11` | - | awaiting review |
| N2-J | Journal module (docs/journal/PLAN.md) | - | - | parked: plan done; build after Phase 0 (OD-1) |
| HB2 | Owner intraday history (flag `history_intraday`) | `claude/hb2-intraday-history` | #202 | merged |
| N3 | U2 remaining blocks: formula block, opening shipped screens | `claude/n3-u2` | - | awaiting review |
| N3 | U3 full gates: gate manifest, definition of done, run replay | `claude/n3-u3` | - | awaiting review |
| N3 | U5 D3a heatmap + chart grid views | `claude/n3-u5-d3` | - | awaiting review |
| N3 | U5 D3b RRG view + template sheet | `claude/n3-u5-d3b` | - | awaiting review |
| N3 | U5 D4 "Then" panel | `claude/n1-x1b` | #231 | merged (with X1b) |
| N3 | U5 D5 quality bar: keyboard e2e, axe, reduce motion, visual regression | `claude/n3-u5-d5` | - | awaiting review |
| N3 | EV3 / EV4 trend strip, replay, drawn-as declarations (flag `evidence_context`) | `claude/n3-ev3` | #240 | PR open; review: MERGE |
| N3 | BK3 VCP, Donchian, squeeze, MA, RSI / ADX regime breakouts | `claude/n3-bk3` | #229 | merged |
| N3 | BK4 trendline / channel, pattern, gap / base, retest breakouts | `claude/n3-bk4` | #241 | PR open; review: MERGE (LOW fixed at 51ba195) |
| N3 | BK5 breakout screen fitness from the shared daily lake | `claude/n3-bk5` | - | review: FIX (MED: unfiltered lake sources) -> fixes at a9eb7a5; re-review: in progress |
| N3 | LG2 Legends cards, set 2 | `claude/n3-lg2` | #242 | PR open; review: MERGE |
| N3 | LG3 point-in-time fundamentals + Graham / Greenblatt / O'Neil cards | `claude/n3-lg3` | - | review: FIX (HIGH tenancy) -> fixes at 4757694; re-review: pass |
| N3 | LG4a Legends card actions | `claude/n3-lg4` | - | review: FIX (MED) -> 1dd0642, re-review: pass; LOW fix at 96d7616, re-check in progress |
| N3 | LG4b fitness sparkline / stats on the cards | - | - | parked: waits for S6 (S6a merged; S6b in PR #243) |
| N3 | S5-C F&O screener functions (flag `screener_fno`) | `claude/n3-s5c` | - | review: FIX (HIGH tenancy, MED) -> re-review: FIX (MED bind limit) -> d3a8032, re-review: pass; main merge c86b5c8, re-check in progress |
| N3 | S6a screen history + fitness | `claude/n3-s6` | #235 | merged |
| N3 | S6b rolling screen backtest | `claude/n3-s6b` | #243 | PR open; review: MERGE |
| N3 | S7a ranking systems | `claude/n3-s7` | - | review: FIX (4 MED) -> 578ffe4; re-review: pass |
| N3 | S8a ownership and events + S8c one engine per venue | `claude/n3-s8` | - | re-review: fail (HIGH: another org's contributed shareholding rows were read) -> fix at d69cefe; re-review: in progress |
| N3 | S8b crypto venue for the ownership / venue screens | - | - | parked: no crypto market-data feed in Phase 0 (crypto paper is v1.3) |
| N3 | S9 screener in the Copilot | `claude/n3-s9` | - | re-review (security fixes): queued, after S8 |
| N3 | S10 ETF screen, WhatsApp / SMS DLT seam | `claude/n3-s10` | #233 | merged |
| N3 | TX2 confluence, mirrors, watch-then-trigger | `claude/n3-tx2` | #238 | PR open; review: FIX (MED) -> 7991b84; re-review: pass |
| N3 | TX3 regime-aware greying, setup-of-the-day digest | `claude/n3-tx3` | - | review: MERGE (merge of TX2 fixes checked) |
| N3 | TX4 learn mode, provenance, forking | `claude/n3-tx4` | - | review: MERGE; LOW fixed, re-check: pass |
| N4 | CH3a price-action layer | `claude/n4-ch3` | #212 | merged |
| N4 | CH3b legs, BOS / CHoCH, liquidity, trendlines | `claude/n4-ch3b` | #218 | merged |
| N4 | CH4a trades layer | `claude/n4-ch4a` | - | review: in progress |
| N4 | CH4b contract and spread charts | `claude/n4-ch4b` | - | review: in progress |
| N4 | CH4c futures basis, carry, calendar spread, rollover (flag `chart_futures_basis`) | `claude/n4-ch4c` | - | review: MERGE; both LOWs fixed at 04946c0 |
| N4 | CH5-a multi-chart workspace | `claude/n4-ch5` | #215 | merged |
| N4 | CH5-b chart types, session breaks, 24x7 axis | `claude/n4-ch5b` | - | review: in progress |
| N4 | CH6a ScreenQL studies on charts | `claude/n4-ch6` | #223 | merged |
| N4 | CH6a re-review LOWs | `claude/n4-ch6-lows` | - | awaiting review |
| N4 | CH6-2 50k-candle timing + render smoke checks | `claude/n4-render-checks` | - | awaiting review |
| N4 | CH7 TradingView Advanced Charts adapter | - | - | parked: waits for TradingView access (owner) |
| N5 | BYOK-V3 Vision reviews on the user's own key | `claude/n5-byok-v3` | #217 | PR open; migration re-chained onto main's head; CI |
| N5 | H-C4 Copilot multiview output checks | `claude/n5-hc4` | - | review rounds 1-6: FIX; round 7 fixes at 1950ce3, re-review: in progress |
| N6 | G Trade engine port | - | - | parked: waits for the owner's "stable" mark per module |
| N7 | C2b fill / slippage / latency models | `claude/n7-c2b` | #236 | merged |
| N7 | C2c span-like margin, black76 pricing | `claude/n7-c2c` | #237 | PR open; review: MERGE |
| N7 | C5 trial ledger + deflation, lake windows, holdout | `claude/n7-c5` | - | review: MERGE; LOW fixes at 0943b84, re-review: pass |
| N7 | C3 portfolio backtest (flag `backtest_portfolio`) | `claude/n7-c3` | - | review: FIX (MED DoS) -> bdd3330; re-review: pass |
| N7 | C2d sampled latency, physical settlement | `claude/n7-c2d` | - | review: MERGE; LOWs fixed at 8e283a5 |
| N7 | C6 distributed backtest sweeps (flag `backtest_distributed`) | `claude/n7-c6` | - | review: FIX (HIGH grid-size DoS, MED stuck sweeps); fixes in progress |
| N7 | D7 rest: client-level F&O position limit (flag `fo_position_limits`) | `claude/n7-d7` | - | review: FIX (MED same-day limits row; docs overclaim); fixes in progress |
| N7 | I1 options analytics (flag `options_analytics`) | `claude/n7-i1` | - | awaiting review; I2+ held (owner: no new slices) |
| N8-N9 | Phase 1 items behind flags; crypto / IBKR / K | - | - | not started: no branches; after N7 in the freeze order, and held while the review backlog is 10 or more |
| Fix | Exit safety: re-exit guard and naked-short guard | `claude/fix-reexit-guard` | - | review: in progress (priority) |
| Fix | AI output filter: trading-call wording in EN/MR/HI (ADR-0006) | `claude/fix-ai-output-filter` | #230 | PR open; review rounds 1-6: FIX; round 7 at e352270, re-review: in progress |
| Fix | Shared fundamentals scope (security) | `claude/fix-shared-fundamentals-writes` | #234 | merged |
| Fix | Shared fundamentals: #234 LOW follow-ups (repair command, refresh scope, symbol squatting) | `claude/fix-shared-fundamentals-lows` | - | review: FIX (2 MED) -> fixes in progress |
| Fix | Phase 0 deployment defaults; Lane C LOWs; sources verified; exit sweep; tax report; savepoints; acting user; gateway injection; dark contrast | various | #204, #209, #205, #221, #225, #220, #226, #228, #227 | merged |
| Lane D | Phase 0 exit criteria in CI: staging stack, acceptance, load-500, rollback rehearsal | `claude/ci-staging-stack` | #222 | PR open (draft) |
| Lane D | Load-500 p95 work (LD-7) - lane D's only job until it passes | `claude/perf-500` | - | in progress: profiling the top 5 costs on the CI runner |
| Lane D | Shared LTP rate budget per broker credential | `claude/ltp-shared-budget` | - | awaiting review (folded into the load-test work if it is one of the top costs) |
| Lane D | CI backend timeout 55 min + slowest-test report | `claude/ci-backend-timeout` | #224 | merged |

Backlog (13 Oct, 02:30 UTC): 17 branches await a first review (N2-8..N2-11, U2, U3, U5 D3a / D3b / D5, CH6-lows,
CH6-2, I1, LTP budget) or a queued re-check (S9, N2-4..N2-7 started), 12 reviews / re-checks are running, and 26 PRs
merged on 13 Oct IST: #202, #207, #209, #211-#216, #218-#221, #223-#229, #231-#236.

### Phase 0 table

| Z | What | Status | PR | % |
|---|---|---|---|---|
| Z1 | PW5 proposal card + PW6 invite gate / feedback | merged | #162, #159 | 100 |
| Z2 | BT1 Tier A per-user broker feed (Upstox, Dhan), entitlement/isolation guard, login reminder, data-tier badges, BROKER_DATA_TERMS gating | merged (flag byob_data_tier off): BT1 + BT1b (guard every live read, credential holder, staleness) | #167, #177 | 100 |
| Z3 | BT2/BT3: proposal -> Telegram/in-app/push -> Approve PAPER, per-user PAPER account, protective exits automatic, approval expiry/re-quote, ladder card | merged (flags approval_only_entries, paper_account off) | #173, #174 | 100 |
| Z4 | HB0 R2 importer + HB1 daily 10-year backfill + nightly EOD jobs; owner intraday store tenant-scoped | merged (flags history_import / history_backfill off); owner intraday history comes from HB2 after Z11 (see note) | #164, #169 | 100 |
| Z5 | P1-c builder page (payoff, draggable strikes, metrics) + P1-d plan-before-trade ticket, risk-by-default caps / lockouts | merged (flags `risk_by_default`, `vix_gate` off): P1-d #179, follow-up #195 (VIX reading per user under byob, member fallback, number-free refusal text), time exits on the worker clock #194 | #170, #180, #179, #194, #195 | 100 |
| Z6 | P1-e journal auto-capture, behaviour tags, discipline score, weekly review | merged (flag discipline_engine off) | #171, #172 | 100 |
| Z7a | Market zones port from Trade (demand/supply, order blocks, S/R, dynamic S/R, level memory; ATP lifecycle fresh -> tested -> flipped -> invalidated) | merged (flag market_zones off): Z7a-1 detection (6528/6528 identical to Trade), Z7a-2 lifecycle/memory/nightly store (5336/5336), Z7a-3 chart layer + ScreenQL zone functions, plus the perf follow-up (batched reads, vectorised zone series, no silent truncation) | #168, #176, #196, #200 | 100 |
| Z7 | U6 BK1 + BK2 breakout screens + quality overlay; U8 EV1 + EV2 evidence charts | merged: BK1 + BK2 (flag screener_breakouts off; worker-side CDS session change parked as OPEN_QUESTIONS BK2-8), EV1 + EV2 evidence charts (flag evidence_charts off; owner-only Telegram image, 90-day retention) | #178, #181, #197 | 100 |
| Z8 | U9 TX1 taxonomy + gallery chips; U7 LG1 (only if Z1-Z7 done) | merged: TX1 taxonomy + gallery (flag screener_gallery off), LG1 eight Legends cards from existing ScreenQL (flag screener_legends off; book sources to verify, LG1-2) | #184, #199 | 100 |
| Z9 | BYOK-V2 Vision audit on proposals (own key, caps) | merged (V1 #160, V2 #163) | #160, #163 | 100 |
| Z10 | Launch hardening (U3 subset G1/G2/G4/G5/G6/G7), load test 500 sessions, staged rollout flags, DPDP/terms/risk pages, support bot | merged: Z10-a no-look-ahead harness + golden/chart parity (#187), Z10-b stale-alert guard + reliable outbox (#189), Z10-c tenancy/scope sweep + no-API-order-path guard + phase0_paper_only lock (#193), Z10-d screener perf + load test (#190), Z10-e staged rollout 50/200/500 + status page + runbooks (#191), Z10-f legal pages + support bot (#188). All new flags off. Owner: the 500-user staging load test | #187-#193 | 100 |
| Z11 | Hostinger deploy bundle (owner-run command, rollback, R2 backup verified) | merged (#186): deploy/atp.sh, sealed and encrypted R2 backups with a non-superuser restore check, backup image with openssl, rollback-safety lint. Owner runs it; blockers: the four owner blockers above (server IP and the R2 token in the host .env apply here) | #186 | 100 |

Also merged: #183 fix - the tax report uses the platform charges table (india_costs) and a configurable F&O turnover method (STT rows from 1 Apr 2026 still marked verify, OPEN_QUESTIONS TX-1).

Data source note: the Trade-repo data collector was cancelled by the owner (2026-10-11), so HB0's R2 importer (#164) stays idle unless data arrives in that store. HB1 (NSE bhavcopy, run on the server) covers the daily history. The owner's intraday history comes from **HB2** (merged #202, flag `history_intraday` off), run on the server.


docs/TRADE_PORT_MAP.md maps Trade -> ATP (Phase 0 ports fold into Z3/Z5/Z10; N-list ports in its section "N-list ports"). Still parked: G (owner's "stable" mark), CH7 (TradingView access), BV vendor tier, Capacitor/Tauri, crypto, IBKR, K. The old BYOK-V3 WIP branch `claude/byok-v3-scan-daily` is superseded by #217.
Not doing: the Trade-repo data collector (owner cancelled it 2026-10-11).

### After Phase 0 (N-list, docs/specs/ATP_PHASE0_FREEZE.md §3)

Started when Z11 merged (12 Oct), in the freeze's order. A Phase 0 bug, beta feedback or a security issue pre-empts
N-work. Every slice and its state is in the N-list table at the top. Plans: N1 docs/design/N1_EXECUTION_CORE.md
(questions N1-1..N1-9), N2 docs/design/N2_P1FGH.md, journal module docs/journal/PLAN.md (JM-1..JM-34), HB2
docs/design/HB2_INTRADAY_HISTORY.md (HB2-1..11), charting docs/design/CHARTING.md, screener docs/design/SCREENER.md.

### Trade port map

docs/TRADE_PORT_MAP.md maps every Trade repo module (Trade@eafc872; the N-list rows at Trade@1e6c9ac) to ATP, with status and parity tests.
N-list ports: E1 (ATP's own, #206), L1 level gate (ported, #210), L2 Daily trend fold (ported, #213), CH3a layer on the ported
engines (#212), CH3b structure / legs / trendlines / liquidity (ported, #218); X1 (merged #214), F (merged #219),
X2 (partial, branch) and BK4 (reuses the CH3b trendline rule, branch).
Each port PR updates its row in the map and these counts.

| Status | Rows (of 196) |
|---|---|
| ported | 17 |
| partial | 18 |
| partial (unverified) | 9 |
| in progress (Z7a) | 4 |
| missing | 48 (25 are G rows that wait for the owner's "stable" mark) |
| not needed | 100 |

Phase 0 ports (Z3 / Z5 / Z10): order safety (partial), charges golden check (partial), strike breach model (ported, Z5 P1-d),
India VIX gate (ported, Z5 P1-d), PAPER journal / Telegram commands (partial), PAPER exit engine (partial), expiry rule
(partial: ported, not wired).

Working rules: docs/WORKING_RULES.md (multitasking and self-review are required). Updated with docs/WORK_LOG.md (at least every 2 hours while work runs). Own PRs are merged when CI is green and review passes (owner's Phase 0 rule); flags stay off.
Order: A -> C1+C4 -> D -> B (ADR -> build) -> E -> C2-C6 -> F -> G1-G2 -> H -> I -> G3-G6 -> J -> K.
Indian PAPER go-live on Hostinger comes before the global work (v1.2); crypto paper (CoinDCX spot) after it (v1.3).

Last update: 2026-10-13 IST (N-list table refreshed 02:30 UTC; refreshed after every merge).

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
| E | Execution core and scale (ADR-0014) | E1 merged (N1): intent queue, routing policy, worker drain (flag `execution_core` off); E2 (CLOSING pre-claim, intents read API, venue contract suite): check | #206 | 15 |
| F | Portfolio risk | F1-F3 merged in #219; on main `claude/n1-f` (flags `portfolio_risk`, `portfolio_risk_caps` off) | #219 | merged |
| G | Trade repo engine port (ADR-0015) | not started | - | 0 |
| H | Copilot v2 (ATP_COPILOT_SPEC): design note, review findings G1-G13 checked | done (design) | #103 | 100 |
| H-C1 | Safety: a server-side evidence, b AI rate limit, c output filter (en+mr), d grounding, e llm_calls hygiene, f prompt_version | a-c done (#103); d-f done (full suite 1364 passed) | #103, #108 | 100 |
| H-C2.. | Tool-calling agent core, evals, research loop, memory, ... | ADR-0019/0020 (provisional); H-C2a core; H-C2b-1 proposal tools + injection guard; H-C2b-2 JSON answer contract + OpenAI tools; H-C2b-3 market/research tools; H-C10a golden sets + CI gate; H-C3a+b research loop (ledger, deflated report, loop + API behind `ai_research`, off; second-pass fixes: OOS warm-up, flat-draft verdict, PBO window, holdout trim) (each full suite green, flags off); H-C3c-1 studies as jobs (queue table, research worker under compose profile `research`, heartbeat, interrupted state) + H-C3c-2 Strategy Lab research panel (suite 1422 passed); H-C4 multi-view analysis behind `ai_multiview` (off), branch claude/n5-hc4 | #115, #117-#121, #135, #142 | 70 |
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

Owner decisions pending (provisional decisions taken, work continues): D-1..D-4 (#87, #94, #95), C2-1/C2-2 (#100), B-1, B-2 (#88), H-3..H-5 (#103), SC-1..SC-6 and U1-Q1/Q2 (#104), SC-7..SC-11 (#125-#138), H-6..H-9 (#121, #135), CH-1..CH-6 (#107, #136), IB-1..IB-5 and CR-1 (#137). Owner blockers (only these four): Hostinger server IP; R2
bucket / token in the host .env; Dhan partner application; company registration.
