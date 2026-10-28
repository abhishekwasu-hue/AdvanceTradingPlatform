# Owner orders - short index

One line per order and its roadmap ID. Status lives in docs/ROADMAP.md (rule R4); the long history of this index is in
README_HISTORY.md. INBOX.md (every instruction, verbatim) and DECISIONS.md are append-only: grep by date or ID.

| # | Order | File | Roadmap | Short status |
|---|---|---|---|---|
| 0 | Step 0: repo state; CLAUDE.md | ATP_HANDOVER_PACK.md | - | done |
| 1 | Font size | ATP_HANDOVER_PACK.md §1 | - | done |
| 2 | Option chain analysis + charts | ATP_HANDOVER_PACK.md §2 | P1-4 (J2) | partial |
| 3 | F&O workspace | ATP_SESSION_FNO_WORKSPACE.md | P1-6, P1-7 | partial |
| 4 | Default watchlists | ATP_HANDOVER_PACK.md §4 | P1-3 | built; sync ON is an owner step |
| 5 | AI Copilot | ATP_HANDOVER_PACK.md §5 | P2-2 | partial |
| 6 | AI keys + page tours (Master Order 2) | ATP_HANDOVER_PACK.md §6 | P2-2, P2-6 | partial |
| 7 | Front page P3 | ATP_HANDOVER_PACK.md | - | done (#329) |
| 8 | Full UX audit + additions (Master Order 3) | ATP_SESSION_UX_AUDIT_MASTER.md | P2-1, P2-6 | partial |
| 9 | Ready-made dish, daily rhythm, plans (Master Order 4) | ATP_SESSION_GROWTH_MASTER.md | P2-4, P5-2 | partial |
| 10 | Analyze a chart | ATP_HANDOVER_PACK.md §10 | P2-5 | built, flag OFF |
| 11 | Strategy Lab, idea -> code / scan, compliance, signal language | ATP_HANDOVER_PACK.md §11 | P2-3 | partial |
| 12 | Idea library templates | ATP_SESSION_IDEA_LIBRARY.md | P2-3 | partial |
| 13 | Strategy template library | ATP_SESSION_STRATEGY_TEMPLATES.md | P2-3 | partial |
| 14 | Wave / correction templates (G1-G12) | ATP_SESSION_WAVE_TEMPLATES.md | P2-3 | partial |
| 15 | Settings redesign + connect AI in one paste | ATP_SESSION_SETTINGS_REDESIGN.md | P2-2 | partial |
| 17 | Production readiness | ATP_SESSION_PRODUCTION_READINESS.md | P3-1 .. P3-5 | partial |
| 18 | Chart interaction + colour (v2) | ATP_SESSION_CHART_AND_COLOUR.md | P1-1, P1-4 | colour done (#447); chart core in progress |
| 19 | Trader review + day-one trader test | INBOX 2026-10-23 | P1-3 | test done (#470); production check pending |
| 20 | Trading terminal master order (Part 6 superseded by row 21) | ATP_SESSION_TERMINAL_MASTER.md | P1-5, P1-7, P2-7, P4-2 .. P4-9 | partial |
| 21 | The ATP Trading Terminal (T1-T7) | ATP_SESSION_TRADING_TERMINAL.md | P1-6, P4-1, P4-2, P4-8 | T1 in review (#493) |
| - | Login error message + `atp.sh reset-password` | ATP_HANDOVER_PACK.md §8d | - | see history |
| - | Google sign-in | ATP_HANDOVER_PACK.md | P5-3 | waiting for the owner (OAuth credentials) |
| - | Login under load (LD-9) | CI | P3-2 | open |
| - | Roadmap (one plan, rules R1-R6) | ROADMAP.md (docs/ROADMAP.md) | all | binding since 24 Oct |
| - | Copilot chart reading (CP-V, CV1-CV13) | ATP_ORDER_CP_V.md | CP-V | fixed spec ✅ (CP_V_SPEC.md); build after DATA-H |
| - | Token budget rules TB-1..TB-9 | INBOX 2026-10-24 | all | binding since 24 Oct |
| - | Combined exit (CX1-CX7) + strategy exits at entry (SX1-SX8) | ATP_ORDER_CX_SX.md | P1-6c, P1-6d | review first |
| - | Trader protection F1-F8 | ATP_ORDER_TRADER_PROTECTION.md | TP-1..TP-8 | after Phase 1 |
| - | Colour accents CA1-CA5 (✅ desktop) + phone CA6-CA8 | ATP_ORDER_UI_H_CA.md | UI-H-CA | #534 |
| - | Dark mode contrast DM1-DM5 | ATP_ORDER_UI_H_DM.md | UI-H-DM | after #534 |
| - | P2-F every feature 100% functional on production (FN0-FN7) | ATP_ORDER_P2F_FUNCTIONAL.md | FN0-FN7 | FN0 first |
| - | Journal v2 J1-J6 | ATP_SESSION_JOURNAL.md | P2-8 | waiting for a free slot |
| - | Modern trading-app look UX-1..UX-10 | ATP_ORDER_UX2.md | UX-2 | UX-1 first |
| - | Numbers first, text on demand U1-U6 | ATP_ORDER_UX11.md | UX-11 | PR 1 primitives + guard |
| - | Numbers first, logical colour (whole-app pass) | ATP_ORDER_UX11B.md | UX-11b | audit re-verify, then PR 1 C1 + C6 |
| - | Professional palette (colour_v3) | ATP_ORDER_UX14.md | UX-14 | colour_v3 tokens + comparison page |
| - | M45 Multi-broker accounts MB-0..MB-9 | ATP_ORDER_M45_MULTI_BROKER.md | M45 | design note ✅ (M45_MULTI_ACCOUNT.md); build MA-1..MA-6 next |
| - | Screener audit + fix pack A-E (27 Oct) | ATP_ORDER_SCR_FIX.md | SCR-FIX | PRs A-E open (#614, #619, #618, #613, #620) |
| - | Trading Brain BR-0..BR-3 (28 Oct) | ATP_ORDER_TRADING_BRAIN.md | BR-0..BR-3 | filed; review in docs/design/BR_REVIEW.md for the owner's ✅; plan only until BR-0 |

Other closed rows (Upstox OAuth redirect, broker field map, `users.ai_language`): README_HISTORY.md.
