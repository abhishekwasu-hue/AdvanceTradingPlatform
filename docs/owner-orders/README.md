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
| - | Screener audit + fix pack A-E (27 Oct) | ATP_ORDER_SCR_FIX.md | SCR-FIX | A-E merged 28 Oct (#614, #619, #618, #613, #620) |
| - | No platform defaults in order placement / risk (28 Oct) | ATP_ORDER_DEF0.md | DEF-0 | PR 1-4 merged; deploy after #641 (acceptance fix) + owner ✅ |
| - | Trading Brain BR-0..BR-3 (28 Oct) | ATP_ORDER_TRADING_BRAIN.md | BR-0..BR-3 | filed; review in docs/design/BR_REVIEW.md for the owner's ✅; plan only until BR-0 |
| - | UI standards audit fix pack A / B / C (28 Oct) | ATP_ORDER_UI_STD.md | UI-STD, UI-STD-C | superseded by UI-28 (file kept) |
| - | Consolidated UI order U1-U7 (28 Oct; replaces the separate UI orders of 27-28 Oct, UI parts) | ATP_ORDER_UI_28OCT.md | UI-28 | filed; owner 29 Oct: SEBI parked, U4 palette + noise sweep first; audit table docs/design/UI_STANDARDS_AUDIT.md; first report next |
| - | Complete audit: verify -> grade -> differentiate (28 Oct) | ATP_ORDER_AUDIT_28OCT.md | AUD-V1..V7, AUD-G1.1..G1.7, AUD-G2.1..G2.5, AUD-T1..T9 | filed; verification in docs/design/AUDIT_28OCT.md; V next |
| - | Autopilot fix pack (28 Oct) | ATP_ORDER_AP_FIX.md | AP-FIX | stub - full text awaited from the owner |
| - | Copilot fix pack (28 Oct) | ATP_ORDER_CP_FIX.md | CP-FIX | stub - full text awaited from the owner |
| - | OI banner v2 (28 Oct) | ATP_ORDER_OI_V2.md | OI-V2 | stub - full text awaited from the owner |
| - | Prices everywhere / after hours (28 Oct) | ATP_ORDER_PX_1.md | PX-1 | stub - full text awaited from the owner |
| - | Terminal rebuild (30 Oct) | ATP_ORDER_TERM2.md | TERM-2 | filed; step 1 next |
| - | Copilot + Strategy builder redesign (30 Oct) | ATP_ORDER_CP_SB_UX.md | CP-UX, SB-UX | filed; after TERM-2 |
| - | Charts + Terminal consolidation (30 Oct, + addendum) | ATP_ORDER_CT_1.md | CT-1 (Terminal parts in TERM-2) | filed |
| - | Full ATP review (30 Oct) | ../reviews/ATP_FULL_REVIEW.md, ../design/REV1_PLAN.md | REV-1 | approved 30 Oct; strategy-test additions S1-S6 |
| - | M55 full audit response (30 Oct; text kept private - security) | owner upload / private repo | M55 | approved 30 Oct, in progress |
| - | Sidebar show/hide + Terminal panel resizing (30 Oct) | ATP_ORDER_M57_M58.md | M57, M58 | not started |
| - | Copilot reading card + Analyze a chart (30 Oct) | ATP_ORDER_M59.md | M59 | not started |
| - | Sidebar / tabs rationalisation (30 Oct) | ATP_ORDER_M60.md | M60 | approved, not started |
| - | Option chain OI / PCR tab fixes (30 Oct) | ATP_ORDER_M61.md | M61 | not started |
| - | Trading journal made working (30 Oct) | ATP_ORDER_M62.md | M62 | not started |
| - | Feature inventory: production truth + build / hide / merge (30 Oct) | ATP_ORDER_M63.md | M63 | Phase 0 |
| - | Order-time risk checks (30 Oct) | ATP_ORDER_M64.md | M64 | not started |
| - | Engine takeover: Trade engine inside ATP (31 Oct) | ATP_ORDER_M66.md | M66 | in progress |
| - | Universal trade settings: no pre-chosen trading defaults (31 Oct) | ATP_ORDER_M68.md | M68 | in progress |
| - | Permanent fix for broker API limits (31 Oct) | ATP_ORDER_M71.md | M71 | in progress |
| - | AI Copilot rebuild - trader-friendly, grounded agent (1 Nov) | ATP_ORDER_M72.md | M72 | not started |
| - | Sign-in page: Google + Facebook login (1 Nov) | ATP_ORDER_M73.md | M73 | not started |
| - | "Continue with your broker" sign-in (1 Nov) | ATP_ORDER_M74.md | M74 | not started |
| - | Hide theory text (30 Oct) | ../reviews/PROSE_INVENTORY.md | PROSE-1 | in progress |

Other closed rows (Upstox OAuth redirect, broker field map, `users.ai_language`): README_HISTORY.md.
