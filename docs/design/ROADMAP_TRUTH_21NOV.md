# Roadmap truth audit (21 Nov)

Why: the owner said "pudhil kaam kr" (21 Nov). The handover's next items (CA-CHAT, TERM-CON, CP-CHAT, BT-DRAW, SFC) were
already merged; many status rows still said "not started" / "in progress" / "draft PR". Each open row was checked against
main (code, flags in backend/app/platform/flag_catalog.py, tests, merges). Read-only check; no code changed.

Verdicts: BUILT = on main (often behind a flag, OFF); PARTLY = some parts on main; NOT BUILT = no trace found.
"Done" still needs (R4): deployed + flag decided + checked on the real site + the owner's ✅.

## Built on main - waits for the owner (deploy / flag / check / ✅)

| Row | Evidence | Owner step |
|---|---|---|
| CA-CHAT | #1111 (19 Nov): backend/app/chart_reader/chat.py, frontend/src/chartReader/ConditionChatV2.tsx; in the 20 Nov deploy | try "My own conditions" on Analyze a chart (flag `chart_reader`); ✅ |
| CP-CHAT | #1132 (20 Nov) | deploy; ✅ |
| TERM-CON | #1135-#1141 | deploy; flag `terminal_conclusion`; ✅ |
| WS-FAST | merged via integration-17nov (#1099) | check on the 218-stock list; ✅ |
| B1 (LIVE-1 amended) | backend/app/platform/live_owner.py (`org_live_open`); integration #1093 | ✅ |
| BIZ-1, BIZ-1b | docs/design/ROUTE3_AUDIT.md; backend/app/me/broker_book.py, flag `broker_book`; integration #1093 | flag `broker_book`; ✅ |
| BIZ-2 | Route 1 runner S1-S8 (#1092), release UI (#1100), flag `user_runner` | runner release; RUN-Q2..Q4 pilot |
| COP-3 | f6a4cf44e | ✅ |
| M58 | frontend/src/terminal/layoutModel.ts (T4 gutters), #1046 | ✅ |
| M59 | CR-1 card, CR-2 attach, CR-4 hybrid (flag `chart_analyze_hybrid`) | flag; ✅ |
| M61 | OI-1 formatCount, PCR-1..3 (option_chain/analyses.py, session_rows.py), OiTabV2 | ✅ |
| M62 | journal/ops.py, entries.py, stats.py, export.py, JournalPageV2 | flag `discipline_engine`; ✅ |
| M64 | risk_engine/hierarchy.py, guardian.py, RiskBlockV2, test_m64_risk_limits.py | ✅ |
| M71 | backend/app/market_store/, flag `market_store_reads` OFF | flag; PL-6 proof |
| M73, M74 | auth/social*.py, auth/broker_signin.py | provider keys (owner setup) |
| M80 | docs/design/M80_FLAG_AUDIT.md, platform/flag_state.py | ✅ |
| M84 | chart_analyze/detect.py | ✅ |
| M86-NB, M88 | market_data/candles_routes.py (M86 D4); journal/importer.py, trade_chart.py | ✅ |
| M45 | backend/app/accounts (multi, fan-out), flag `multi_account` | flag; ✅ |
| PAPER-NR | risk_engine/paper_nr.py | ✅ |
| PROSE-1 | guard 1072dbdb3 (integration #1093) | ✅ |
| TERM-2 | frontend/src/terminal (ContextBarV2, TerminalGridV2, PanelsV2...), flags term_* OFF | flags; ✅ |
| CP-UX, SB-UX, M55 | CU1-CU7 / SU1-SU8 / M55 markers on main | ✅ |
| DATA-H, P1-8, P2-1, FN0 | market_lake backfill; watchlists flags; P2_1_FLAG_AUDIT.md; FN0 docs | owner runs / turns on; ✅ |
| P2-5 | Analyze a chart built (flag `chart_analyze`) | the owner turns it ON as beta (a flag decision) |

## Partly built

| Row | On main | Left |
|---|---|---|
| P1-4 | view memory, embedded-chart touch e2e | phone recording; ✅ |
| P1-6 | Terminal rebuild (`terminal` ON) | TT-9 / TT-10 / T3 check |
| AUD-G1 | G1.1, G1.3, G1.4 p1, G1.5, G1.6 p1, G1.7 | G1.2 owner's company details; G1.4 p2 Sentry DSN |
| AUD-T4..T9 | T4-T8 flags + code | T9 pricing (owner) |
| FN2-FN7 | FN2 audit + items, FN6 note, runner | FN3-FN5 / FN7 items unverified |
| P2-2 / M72 | planner + PR-2..PR-11 behind copilot_* flags | the owner-run eval (M72Q-7); flags; J5 check |
| P2-3 | template library, test chart | J3 proof run |
| P2-4 | daily rhythm (flag `daily_rhythm`) | flag ON + Telegram check |
| P2-6 | help centre + 12 page tours (flag `help_centre`) | remaining pages' tours; J7 |
| P2-7 | Performance routes | options-seller positions view unverified |
| P2-8 | journal capture, review, import, own tab | check-in / if-then plans / My proof (J2-J6) not found |
| P3-2 | status history, incidents, alert outbox | external "site down" alert path (owner) |
| P3-4 | checklist, processors, erasure / export | 6 missing rows, lawyer items |
| M66 / M79 | engine_provider, flag `trade_engine` OFF | EN-4 parity CI, EN-6 / EN-7 owner steps (atp-private) |
| M86 | W1 / W2 / W4 (chart_reader) | W3 Copilot cards, proof on 3 real charts |
| SRV2 | srv2bot P1 merged | P2-P5 (P3 has its own running session) |
| STRAT-X | B12, H10, D9 templates | relative-strength template |
| REV-1 | M1-M3 markers and tests | owner ✅ on the comparison pages |
| CT-1 | C1, C3-C6, C8-C10, L1 | C2, L3 unverified |
| BR-1 | setup recall (M3), copilot memory (M5) | M1 shared reader, M2 episodes, M4 profile; owner review |
| TP-1 / TP-2 / TP-4 / TP-6 / TP-7 | ticket max loss; naked-short warning in the spread builder; risk options; journal capture; term_whatif / term_roll (OFF) | breakeven / margin card; positions-list warning; stress test |
| TP-3 | trading/cost_meter.py (AUD-T7) | per-month view unverified |
| TP-5 | zone functions in bar-close alerts (alerts/barclose.py) | false / confirmed break and combined-SL distance alerts |
| P4-5 | chain history (#465, #471) | analytics on it |
| P4-6 | broker GTT + trigger plans (execution/broker_gtt.py, trigger_plans.py, M75 W-9) | iceberg, SIP |
| P4-7 | sector heatmap on the screener board (flag `screener_board_views`) | a market-wide sector heatmap page, if still wanted |
| P4-8 | bar replay (frontend/src/charting/replay.ts) | the reader overlay in replay; Terminal T7 |

## Not built

| Row | Note |
|---|---|
| BR-2, BR-3 | after BR-1 and the owner's review (in order) |
| P4-1 | Terminal T5 + T6 (algo panel, discipline tools) |
| P4-4 | strategy control bar + profit ladder |
| P4-9 | position helpers + AI risk questions (term_roll covers the roll what-if) |
| P5-1..P5-5 | the last phase (marketplace OFF) |
| TP-8 | superseded by M72 PR-8 coach + the journal weekly review |

Method note: verdicts came from three parallel code searches, re-checked by hand for every "not built" claim (five of
them were found partly built). A row marked "unverified" was not checked in depth.

## P2-8 Journal v2 - what is on main (for the next build, NEXT-Q1 default)

Order: docs/owner-orders/ATP_SESSION_JOURNAL.md (3 draft PRs, no merge before the owner's ✅).

| Part | On main | Missing |
|---|---|---|
| J1-1 broker trades | file import (journal/importer.py, source BROKER), read-only broker book (me/broker_book.py) | automatic sync of trades placed in the broker app into the journal, matched to ATP trades |
| J1-2 chart picture | journal/trade_chart.py renders a PNG for manual / imported trades | a stored entry / exit snapshot for ATP's own trades, shown in reviews; delete with the account |
| J1-3 spread view | leg_group_id grouping (journal/service.py, stats.py) | check: one entry with combined P&L, planned vs actual max loss, credit |
| J1-4 plan vs actual | planned values in capture.py | check: one line per entry showing only differences |
| J2 check-in | none (no feeling / confidence / plan-followed fields) | all |
| J3 reviews | weekly review (journal/review.py, WeeklyReviewV2) | daily card, monthly page, feeling / confidence vs result, first-20-trades rule |
| J4 My proof | none | all |
| J5 if-then plans, process goals | none | all |
| J6 delivery, page tabs | journal own tab (#1115) | Telegram / e-mail delivery, tabs Today · Trades · Reviews · My proof · Plans, page tour |
