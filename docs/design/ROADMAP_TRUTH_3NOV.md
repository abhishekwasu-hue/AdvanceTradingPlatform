# ROADMAP truth check (3 Nov 2026) - which status-table rows are really open

Scope: every row of the docs/ROADMAP.md status table marked "in progress", "not started", "not started (parallel)",
"waiting (for owner)", "Phase 0 started" or "in review" (the in-review rows in one block at the end), checked against
`main` at a7a779b93 (Merge #905). docs/ROADMAP.md itself is not edited here.

Method: `git log` (merge titles carry the branch / PR number), grep of the named files and of
`backend/app/platform/controls.py` / `flag_catalog.py` / `flag_state.py` (M80 states), and the order section the row
points to. Production values are not known from the code (OWNER_RULES A.2): a flag's code default is never read as
its live value.

Legend:
- **DONE** - every build part is merged; what is left is only the standard R2 close (deploy, real-site check, owner ✅).
- **OWNER** - the code is merged; something specific from the owner is needed (a flag switch, a key / account, a
  decision, a text).
- **PARTLY** - some parts merged, named parts not built.
- **OPEN** - nothing of the row's own scope built.
- Size of what is left: S (< 1 PR, small), M (1-2 PRs), L (3+ PRs or an order-path change).

## 1. The table

| ID | Status found | Evidence (PR / commit, file:line) | What is left - concrete next step | Size |
|---|---|---|---|---|
| UI-H | PARTLY (in practice superseded) | Audit #503, Wave 0 #510 (25 Oct); the page waves were carried as UX-10 inside UX-2 (ATP_ORDER_UX2.md:13; #577 #579 #581 #582) and then UI-28 U1-U7 (owner ✅ 30 Oct). No commit names "Wave 1..5" | Owner decision: mark UI-H superseded by UX-2 / UI-28 (default) - no separate wave build | S |
| UI-H-BUG | DONE | C1 #506, B2 #507 (+#516), B3/B4 #508, A1 #505, B1 #509; BUG-M1 (phone TF row, title) closed by TF-1 #540 + H9 #559 | Deploy + owner check of C1 / B1 on the site | S |
| UI-H-DM | DONE | 87feacb9e DM1-DM5 + High contrast, merged #536 (27 Oct); frontend/src/ui/darkContrast.test.ts, settings/model.ts | Deploy; owner ✅ on the dark phone page | S |
| UX-2 | PARTLY | PRs 1-7, 9-11 merged (#559 #560 #563 #564 #572 #574 #578 #577 #579 #581 #582 #583), OC-4 #593; OC-5 spot answered by B2b (backend/app/option_chain/as_of.py header) | PR 8 not built: UX-3 watchlist panel docked left in the Terminal + TA-4 layout (ATP_ORDER_UX2.md:18, :26); `grep -ril watchlist frontend/src/terminal` = none. Build it on the TERM-2 panels | M |
| UI-H-TF | DONE | TF-1 #540 (26 Oct); frontend/src/ui/TimeframePicker.tsx used by chart/ChartToolbar.tsx:180, :207, :274; TimeframeTabs.tsx:12 says "replaced on every page" | Deploy + phone check | S |
| SB-1 | DONE (as M57) | #784 m57-m60-sidebar (31 Oct): backend/app/me/prefs.py:44 `shell` = full menu or icon rail, per user; e2e m57-rail-1440 baselines | Deploy + owner check (same as M57) | S |
| DATA-H | DONE (code) / OWNER (run) | DH1-DH6 #515 (index_history.py, own_bars.py, migration d4a6c8e0b2f1), DH2/DH6 #570; M71 PL-1c backfill #811, PL-7 seed #833 | Owner: after deploy, `atp.sh lake-backfill --check` + `atp.sh health` (DH4 bar counts) output to the session; this closes the BR-0 "DATA-H backfill" gate | S |
| CP-V | PARTLY | Spec #555 (CP_V_SPEC.md:34-64, flag `copilot_chart_read`); grep `copilot_chart_read` = 0 hits. Related merged: M59 CR-4 hybrid #777 (chart_analyze), M72 PR-5 Copilot chart reading #875 | Owner decision: does M59 CR-4 + M72 PR-5 replace CP-V (default: yes, mark superseded) - else build CV1 per-TF status, CV3 validator + reads table, CV5 daily limits | S (decision) / L (build) |
| P1-3 | OWNER | Day-one test #470 + fixes #475-#483; universe sync #456 (`atp.sh lists-sync`, `env-set`); trader pack backend/app/platform/trader_pack.py:45 | Owner: deploy, "Enable trader features", `atp.sh lists-sync`; then the session runs the day-one test on production with screenshots | S |
| P1-4 | PARTLY | Chart core PR 1-3 (#421 #425 #438); Auto / Reset / Latest + gesture e2e frontend/e2e/chart-axes-v2.e2e.ts; under ui_v2 the old windows redirect to ChartV2 (App.tsx:332-335) | Still on ProChart without Auto / Reset / Latest: `?contract=` spread window (App.tsx:341-343, pages/ContractChartWindow.tsx) and the v1 pages (MarketPulseCard, PositionChartCard, SignalsPage). Move the spread window to ChartV2; record the phone gestures | S-M |
| P1-5 | OWNER | Data hub #464 (flag `data_hub`, in the trader pack trader_pack.py:55); M80 state "Needs your setup: broker with a live stream" | Owner: "Enable trader features" with Upstox connected; session: API-call numbers before / after (M71 PL-6 gives them) | S |
| P1-6 | PARTLY | T1 #493, `terminal` ON by default (controls.py:638), placeholders hidden #539, T2 TT-9 / TT-10 #602; Terminal rebuilt by TERM-2 (#714 #715 #718 #725 #719) | TT-1 LIVE manual order path not built (DECISIONS row 77 approved it): execution/broker_gtt.py:16, trigger_engine.py:26, flag_state.py:224 `broker_gtt` "not built" -> "TT-1". Build the order-intent + worker sender behind the Phase-0 lock (LIVE switch stays the owner's) | L |
| P1-8 | OWNER | Server lists + Charts panel (WL1), Watchlist page + phone bar tab #554; `watchlists` in the pack (trader_pack.py:64) | Owner: "Enable trader features" + `atp.sh lists-sync`; session: live-price check + ✅ screenshots | S |
| CP-FIX | OWNER | docs/owner-orders/ATP_ORDER_CP_FIX.md: stub, text lost (29 Oct); its CP1 / CP3 built inside CP-UX (ATP_ORDER_CP_SB_UX.md:60-61) | Owner: resend the text, or close CP-FIX as covered by CP-UX (default: close) - it is a BR-0 gate | S |
| AUD-G1.1..G1.7 | PARTLY | G1.1 #654, G1.3 #652, G1.4 p1 #655, G1.5 #670, G1.6 #656 + #708, G1.7 #657 #671 | G1.2: no docs/runbooks/BREACH.md (AUDIT_28OCT.md:23) - session can write it now; the fiduciary / grievance placeholders (legal/content/privacy.md:14) need the owner. G1.4 p2: no `@sentry/react` in frontend/package.json - owner DSN. G1.6: mypy still gates listed packages only (backend/mypy.ini:5-8 names ai, backtest, fundamentals, brokers, workers next) | S + S + M |
| AUD-T4..T9 | OWNER (T9 only) | T4 #695, T5 #707, T6 #703, T7 #688, T8 #691 merged | T9 pricing: owner decision (lawyer before charging) | S |
| P1-7 | OWNER | Smart execution #474 (+ Part 7 PR B); `smart_execution` in the pack (trader_pack.py:61); M80 "Ready" | Owner: "Enable trader features"; session: basket confirm checked on production | S |
| FN0 | DONE | docs/design/FN0_FUNCTIONAL_AUDIT.md #538, FN0_JOURNEYS.md #542; P2-1 merged into it | Owner ✅ on the table | S |
| FN1 | OPEN | `grep -i "definition of done" docs/WORKING_RULES.md` = none; no FN1 checklist file | Write the definition of done into WORKING_RULES + docs/FN1_CHECKLIST.md (FN0 audit :193) | S |
| FN2 | OPEN (gaps) | FN0 :98-131 gaps still true: no Aroon / Ichimoku / abs / OIChange in backend/app/screener; screener/universes.py:4-5 lists system lists only (own watchlists missing); no "add to watchlist" from results; no "only when the set changes" alert | FN2 PR (a) indicators, (b) own watchlist universe + add from results, (c) scheduled run + change-only alert | L |
| FN3 | OPEN | `grep -ril custom_indicator backend/app` = none | Named saved custom indicators (migration) usable in screens, then chart preview | L |
| FN4 | PARTLY | SX exits built (#603, P1-6d); strategy_engine/declarative.py still AND-only (no OR group) | OR groups + full registry operands in the builder; time windows as settings; parity on production | L |
| FN5 | PARTLY | Call log stores inputs + outputs (db/models.py:2608 LlmCallRecord); own key + test; M72 PR-1..11 (#834 #870-#881) | `ai_research` has no screen (flag_state.py:110 "not built" -> FN5): build the start / results screen, or keep hidden | M |
| FN6 | PARTLY | docs/design/D_SEBI.md (design), admin Compliance section #671 (CompliancePanelV2.tsx) | The one owner note "what ATP does / misses / owner + lawyer steps" (FN0 :181-190 lists the facts) | S |
| FN7 | OPEN | no FN7 proof pages | After FN1: one proof page per feature (production phone + desktop, recording) | L |
| P2-1 | DONE | Audit #556 (P2_1_FLAG_AUDIT.md), applied #598 (fates in the admin list); M80 #855 replaced the labels with live states (flag_state.py) | Owner ✅ already given 26 Oct; deploy | S |
| P2-2 | OWNER | Copilot jobs (#354 etc.), CP-UX CU1-CU7 (#717 #722 #724), M72 planner + toolkit (#870-#881); `copilot_agent` needs an AI key (M80) | Owner: AI key + `atp.sh ai-eval` (M72Q-7); session: J5 check on production | S |
| P2-3 | OWNER | `strategy_templates`, `idea_library` Ready (M80) and in the pack (trader_pack.py:62, :71) | Owner: pack ON; session: adopt a template -> paper signal on production (J3). Note REV-S3 (G1-G11 give 0 trades) is in REV-1 | S |
| P2-4 | OWNER | MO4 slice 1 #373; flag_state.py:113 `daily_rhythm` Ready, "messages need Telegram" | Owner: pack ON + Telegram bot (owner task list) | S |
| P2-5 | OWNER | Analyze a chart A1-A4 (#351 #380 ...), M59 #766 #771 #777, M84 #867; flag_state.py:184 needs the private package + a vision key; :188 `chart_analyze_hybrid` waits for the owner's ✅ | Owner: private engine on the server (A1 install), a vision key, ✅ on hybrid | S |
| P2-6 | PARTLY | Help centre C12 #439, page tours MO2 #345; flag_state.py:140 `support_bot` "not built: help pages not complete", :141 `help_centre` needs a support contact | Write the missing help pages (then support_bot); owner: support WhatsApp / e-mail | M |
| P2-7 | PARTLY | Positions to broker standard UX-15 #639, Exit all / groups (trading/ExitAllV2.tsx), privacy mask (terminal/StatusBarV2.tsx:12); Performance = /api/performance/summary (performance/routes.py) with win rate, PF, avg win / loss only (trading/analytics.py:17-29) | Master Part 2 missing: filters + range + CSV, charges / net, streaks, drawdown, Sharpe, expectancy, daily P&L, equity curve, exit-reason donut, calendar heat map (pages/AnalyticsPage.tsx has none). Part 4 missing: MTM-vs-spot and premium-decay charts | M-L |
| P2-8 | PARTLY | M62 #767 #773: notes, tags, checklist, images, manual trades, chart snapshots, stats, CSV / PDF (journal/entries.py, stats.py); spread legs as one unit (leg_group_id, stats.py:203) | ATP_SESSION_JOURNAL.md not built: J1-1 broker sync, J2 check-in (feeling / plan followed), J3 daily + monthly reviews, J4 My proof, J5 if-then plans + process goals, J6 delivery + tabs | L |
| M45 | DONE | MA-1a #568, MA-1b #569, MA-2 #571, MA-3..MA-6 via #580, owner rows #591, MB-7 #600; flag_state `multi_account` Ready | Deploy + owner check (LIVE fan-out only on his LIVE words) | S |
| P3-1 | OWNER | A1 #422: backup / seal / R2 copy, retention, `atp.sh restore-offsite`, CI rehearsal deploy/ci/offsite_rehearsal.sh | Owner: R2 bucket + token + key escrow (runbook 6); then one `atp.sh restore-offsite` drill | S |
| P3-2 | OWNER | A2 #428: observability/ops_alerts.py (every 5 min via `atp.sh ops-check`), outside check .github/workflows/uptime.yml, status page AUD-T8 #691, worker alerts #822 | Owner: register the ops Telegram chat (or OPS_ALERT_TELEGRAM_CHAT_ID), SMTP, repo secrets UPTIME_TELEGRAM_BOT_TOKEN / _CHAT_ID, the free outside uptime service (runbook 10b) | S |
| P3-3 | DONE | A3 #437; `atp.sh rollback` rehearsed in CI (.github/workflows/staging-stack.yml:268-279, deploy/ci/rollback_rehearsal.sh) | Optional: one rollback on production by the owner | S |
| P3-4 | PARTLY | A4 security (dfb00b89b), DPDP export / erase G1.1 #654, docs off in production G1.3 #652, log redaction G1.4 p1 #655 | One security + DPDP checklist with the lawyer items (G1.2 BREACH runbook, placeholders, Sentry DSN) | S |
| P3-5 | OWNER | legal/content/privacy.md, terms.md with [COMPANY NAME] placeholders | Owner: lawyer text, company details | S |
| TP-1 | PARTLY | M76 MP-2 check card on ticket + basket (paper/pretrade_checks.py, fno/CheckCardV2.tsx; flag `trade_warnings` OFF): types at me/trade_settings.py:149-179 | Compare with F1's list (max loss, breakevens, margin with hedge, % of capital, zone distance, events) line by line; add what is missing to the same card | M |
| TP-2 | PARTLY | Only the classic Terminal spread builder marks unhedged sells (terminal/SpreadBuilderV2.tsx:30); no naked-short type in the check card (trade_settings.py:149-179), none on positions | Add a `naked_short` warning type to the M76 card + a positions chip | S-M |
| TP-3 | PARTLY | AUD-T7 cost meter #688 (trading/cost_meter.py: charges vs gross, month / year, per trade) | Slippage vs the price at order time not built | S-M |
| TP-4 | PARTLY | M64 #776: every order-time check a user switch, OFF by default; max_trades_per_day (risk_engine/risk_limits.py:71), loss-streak pause | "A stop may only move in the trade's favour" exists only as the engine rule R7 (risk_engine/guardian.py:30), not as a user rule on a manual stop modify | S |
| TP-5 | PARTLY | Copilot watch #877 (ai/watch.py: near_levels, strike_distance, greeks_jump, oi_shift) | Zone-entry, false-break and confirmed-break alerts not found (`grep -i false.?break backend/app/alerts` = none); WhatsApp waits for the number | M |
| TP-6 | PARTLY | Z6 auto journal + tags, weekly review (#172), M62 chart snapshots | Reader context (zone / state / entry model) needs P5-1 | S (after P5-1) |
| TP-7 | PARTLY | TERM-2 R1 roll / R2 what-if (#725, flags term_roll / term_whatif), Copilot strike_distance card | Portfolio stress grid (master 9.2) not built (`grep -ril stress backend/app` = none) | M |
| TP-8 | PARTLY | M72 PR-8 coach on his journal week #878 | Uses CP-V chart reading (see CP-V decision) | S |
| BR-0 | OWNER (gate) | SCR-FIX, AP-FIX, DATA-H merged; CP-FIX text lost; P5-1 not ported (charts/read_chart.py:19 "not in ATP yet") | Closes when CP-FIX is closed / restated, DATA-H run is checked, and P5-1 lands (M79) | - |
| BR-1 | PARTLY (out of order) | M3 similar-setup recall = AUD-T6 #703 (`setup_recall`); M5 chat memory = `copilot_memory` (M80 Ready) | M1 one shared reader = P5-1; M2 episodes, M4 method profile - only after BR-0 | L |
| BR-2, BR-3 | OPEN | none | After BR-1 | L |
| TERM-2 | DONE | Steps 1-5 merged: #714 context bar, #715 panels, #718 layouts, #725 R1-R4 (flags term_roll / term_whatif / term_strike_area / term_margin_pair, flag_state.py:172-175 Ready), #719 text | Deploy; owner ✅ per step + full-flow proof | S |
| CP-UX | PARTLY | CU1 #717, CU2 + CU3 #722, CU4-CU7 #724 | CP1 not built (CP_SB_AUDIT.md:65-72): chart reader on the user's broker candles per TF - needs the owner's ruling "broker candles replace or back the lake bars"; CHART_NOTE copilot_chat.py:44 stays until P5-1 | M |
| SB-UX | DONE | SU4 #721, SU1-SU6 #723, SU7 + SU8 #728 | Deploy + owner ✅ | S |
| CT-1 | DONE | Audit #720; C1 + L1 #726, C3 + C5 #727, C4 (45c1f85a1, #720), C6 + C8 #729, C10 #730; C2 / C9 / L3-Terminal in TERM-2 step 2 / 5 (CT_AUDIT.md:25, :32, :36) | Deploy + owner ✅ on the comparison pages | S |
| REV-1 | PARTLY | Plan #731; M1 H1-H5 #735 #737; M2 #739 #755 #760; M3 #759; S1 #736, S3 #738, S5 #741, B5 #740 | (1) `atp.sh review-checks` (REV1_PLAN.md:65-70, the ten live checks) is not in deploy/atp.sh - build it; (2) REV-S2 (stop before the time exit on the same bar) - no commit names it; (3) owner: D2 (built-ins' own stop / target), REVQ-1..4 | S + S |
| M55 | DONE (named parts) | S1-S8, T1-T5, O1-O3, Q1, C11, A18 merged (#742-#774); the "rest" list is in the private order text (README :60), not checkable here | Session with the private text confirms the rest; deploy | S |
| M58 | DONE | 66d23802c, #763 (gutters grow / shrink, Panels steppers) | Deploy + owner ✅ | S |
| M59 | OWNER | CR-1/3/5 #766, CR-2 #771, CR-4 hybrid #777 | Owner ✅ on hybrid (flag_state.py:188) + private engine on the server | S |
| M61 | DONE | OI-1..OI-5, PCR-1..PCR-3 (#778 #782); OI-6 / PCR-4 are the tests / proof items | Deploy + proof screenshots | S |
| M62 | DONE | JN-1/2 backend, JN-3/4 page (#767 #773); `atp.sh journal-status` / `journal-backfill` | Owner: `journal-status` output + `discipline_engine` ON for his tenant (JN-1) | S |
| M63 | PARTLY | I-2 inventory #769 (docs/FEATURE_INVENTORY.md), I-7 quality floor #788-#805 | I-1 production table + I-3 pack on the owner tenant (owner run); I-4 / I-5 / I-6 not done: e.g. Terminal Bots / Payoff placeholders still in terminal/PanelsV2.tsx:474-490; duplicates kept until new homes (M60 row) | M |
| M64 | DONE | #776 (Settings > Risk limits, each check a switch OFF; org ceilings off) | Deploy + owner ✅ | S |
| M66 | superseded by M79 (v1 parts) | EN-1 #801, EN-2 #804, EN-3a #806 | Continues as M79 | - |
| M71 | OWNER | PL-1..PL-5, wave 2, PL-7 seed #833; flags market_store_reads / MARKET_FEED_ENABLED / MARKET_BACKFILL_ENABLED OFF | Owner: deploy, turn the three on; session: PL-6 proof (calls / day before-after, NIFTY 50 1H scan) | S |
| M72 | OWNER | CP-0 #829, PR-1a #834, PR-1b..PR-11 #870-#881 | Owner: `atp.sh ai-eval` live eval (M72Q-7), AI key, decision on `copilot_ticket` (flag_state.py:217) | S |
| M73 | OWNER | Social login #832 (+ reviews), nav guard #869 | Owner: Google / Facebook app credentials (owner setup block) | S |
| M74 | OWNER | Broker sign-in #830 (Upstox, Kite, Fyers) | Owner: apply block (platform broker apps) | S |
| M79 | PARTLY | W5 #857, port map v2 #859, W8 #863, W10 #865; flag `trade_engine` needs the private package (M80) | Layer parity T1-T14 lives in atp-private (not checked here); owner: private tag / package on the server | L |
| M80 | DONE | #855 + 40de27eec, 399f26583 (flag_state.py) | Deploy + owner ✅ | S |
| PAPER-NR | DONE | #866 (7c0a480f3 corrected scope, db9aae9ea) | Deploy + owner ✅ | S |
| M84 | DONE | #867 (d2ab6e071) | Deploy + owner ✅ | S |
| STRAT-X | PARTLY | 7a9d0faa3 (in #779): T-PDHL, T-52WH, T-WDP, stop / targets labelled suggested; built-in min_rr removed (REV-B2) | T-RS (RS vs NIFTY) design only: docs/design/STRATX_TRS_DESIGN.md:3 - owner picks A / B / C | S (decision) / M |
| PROSE-1 | DONE | #732 (guard + groups), M68 PR-7 prose guard b833f193e | Deploy + owner ✅ on the before / after page | S |
| P4-1 | PARTLY | T6 limits = M64 #776, kill switch + privacy mask on the status bar (StatusBarV2.tsx:12) | T5 Bots & signals panel is a placeholder (terminal/PanelsV2.tsx:474-480); position origin tags / scoped Exit all not verified | M |
| P4-2 | PARTLY | R1-R4 (#725), Copilot what-if tool (ai/tools/options_kit.py:193) | Payoff panel placeholder (PanelsV2.tsx:488-490); ATM straddle / premium decay / MTM-vs-spot / IV+VIX charts; stress grid | L |
| P4-4 | OPEN | Freeze / Force stop / Reduce bar: no match in backend/app/deployments; profit ladder "will use" trading/trade_split.py:6 (not built) | Control bar + ladder (ExitRules tiers, parity) | L |
| P4-5 | PARTLY | Chain history #465 #471; OI build-up classes (option_chain/analyses.py, oi_regime.py) | IV Rank / IV Percentile, intraday IV / straddle charts (`grep -ril iv_rank` = none) | M |
| P4-6 | PARTLY | Trigger plans Ready (flag_state.py:225), broker GTT path built but `broker_gtt` not built -> TT-1 (:224); freeze slicing in smart execution; OCO-style combined exits (execution/strategy_exits.py) | ATO, iceberg as an order type, SIP not found; GTT needs TT-1 | L |
| P4-7 | DONE | Markets page heat map (markets/MarketsPageV2.tsx:27, :205), `markets_view` Ready (flag_state.py:152), in the pack | Owner: pack ON (needs the nightly download) | S |
| P4-8 | PARTLY | Evidence-chart replay in the screener (screener/EvidencePanel.tsx); reader overlay needs P5-1 | Market replay with paper orders + Terminal T7: no replay in frontend/src/terminal or chart | L |
| P4-9 | PARTLY | Roll what-if R1 (`term_roll`), expiry checklist AUD-T2 #685, Copilot options toolkit M72 PR-4 #874 | "What if NIFTY falls 300" needs the 9.2 stress grid (same gap as TP-7) | M |
| P5-1 | OPEN | charts/read_chart.py:19, copilot_chat.py:44 "not in ATP yet" | Waits on the Trade answer key, then M79 | L |
| P5-2 | PARTLY | backend/app/billing/razorpay.py, webhooks.py | Owner: Razorpay account; Journal paid-plan gate (ATP_SESSION_JOURNAL J6) | M |
| P5-3 | OWNER | `self_signup` (decision), Google sign-in auth/google_routes.py, M73 social.py | Owner: OAuth credentials + the sign-up decision | S |
| P5-4 | PARTLY | docs/design/MOBILE_APP_PLAN.md, MOBILE_PUSH.md, backend/app/mobile/devices.py | Owner: Play Console / Firebase; app build | L |
| P5-5 | OWNER | marketplace code exists; last phase by order | Owner decision (about six months out) | - |

In-review rows, checked: all merged - P1-2 #494, P1-2b #497, P1-6c #524 #527 #529 #553 #603, P1-6d #603, P1-B1 #575
#595, SCR-FIX A-E #614 #619 #618 #613 #620 (no SCR-FIX PROOF page in docs/), ADM-1 #711, M57 + M60 #784, P4-3 #473 #481
(connect guides now from BRK-1 #902). M76 / M77 "open" parts merged in #903 (ca9eba8ec, 3 Nov). Each needs only
deploy + owner check.

## 2. What the evidence says about the R2 count

Of the rows marked "in progress", only P1-4, P1-6 (TT-1), UX-2 (PR 8), REV-1 (two items), M63, M79, AUD-G1.x and
CP-UX (CP1) still have build work on `main`'s evidence. Most others are DONE or OWNER: the bottleneck is one deploy and
the owner's switches, not code.

## 3. Next 5 to build, in roadmap phase order (R3; Phase 1 first)

1. **P1-6 TT-1** - the LIVE manual order path (order intent + worker sender) behind the Phase-0 lock; unblocks
   `broker_gtt` and LIVE trigger plans. Order path: main model, own PR, LIVE switch stays the owner's. (L)
2. **UX-2 PR 8** - UX-3 watchlist panel docked left in the Terminal (TERM-2 panels) + TA-4 default layout. (M)
3. **P1-4** - the `?contract=` spread window on ChartV2 (Auto / Reset / Latest); phone gesture recording. (S-M)
4. **REV-1 leftovers** - `atp.sh review-checks` (read-only counts, the ten live checks) and REV-S2 (stop before
   time exit on the same bar, tests for both orders). (S)
5. **AUD-G1.2 / G1.6** (Phase AUD) - docs/runbooks/BREACH.md with the 90-day SLA (placeholders kept for the owner's
   details), then the next mypy packages (app/mobile, then the 1-3-finding packages). (S + M)

Then P2-F: FN1 (S, docs) -> FN2 -> FN3 -> FN4 (max 3 in progress).

## 4. Owner items (only the owner can do these)

- Deploy main (no migration pending per docs/HANDOVER_NOW.md) - closes most DONE rows.
- "Enable trader features" + `atp.sh lists-sync` - P1-3, P1-5, P1-7, P1-8, P2-3, P2-4, P4-7.
- `atp.sh lake-backfill --check` + `atp.sh health` output - DATA-H, BR-0.
- CP-FIX: resend the text or close it as covered by CP-UX (default close) - BR-0.
- CP-V: superseded by M59 CR-4 + M72 PR-5? (default yes). CP-UX CP1: broker candles replace or back the lake bars?
- UI-H: mark superseded by UX-2 / UI-28 (default yes).
- R2 bucket + token + key escrow, then one `atp.sh restore-offsite` drill - P3-1.
- Ops Telegram chat / SMTP / UPTIME_TELEGRAM_* repo secrets / outside uptime service - P3-2, P2-4.
- Company name, address, grievance officer; lawyer text - AUD-G1.2, P3-5. Sentry DSN - AUD-G1.4. WhatsApp number - G2.5, TP-5.
- Pricing (AUD-T9); Razorpay (P5-2); Google / Facebook OAuth (M73, P5-3); broker platform apps (M74); Play Console (P5-4).
- M71: turn market_store_reads / MARKET_FEED_ENABLED / MARKET_BACKFILL_ENABLED on. M72: `atp.sh ai-eval`, `copilot_ticket`.
- Private engine on the server + vision key + ✅ on `chart_analyze_hybrid` (P2-5, M59); private tag for M79.
- Decisions: STRAT-X T-RS A / B / C; REV-1 D2 + REVQ-1..4; BRK1Q-1..4; support contact for `help_centre` (P2-6).
- v1.0.0 tag + branch prune (AUD-V7, proxy 403 for the session).
