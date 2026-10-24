From Abhi — THE ATP ROADMAP: one common file for the whole project (English). Save it as docs/ROADMAP.md and keep it current.

## Why this file exists
- ATP has 21 owner orders (README rows 0–21). Most of them are "partial", and about 20 features sit behind flags that are OFF or "unfinished".
- Work has been spread over too many items at once. The owner cannot see one finished product, and tokens are spent re-reading long orders and long reports.
- This file is the ONE plan. It does not replace or rewrite any owner order: it puts them in order, defines "done", and limits how much runs at once.

## Rules (binding; add them to DECISIONS)
- **R1. One plan.** Every task belongs to a roadmap item below (an ID such as P1-3). A new owner instruction is placed into this file (a new item or a change to one), in the same turn it is recorded in INBOX. Nothing is built outside the roadmap.
- **R2. Finish before start.**
  - At most 3 build items "in progress" at a time (plus fixes the owner asks for).
  - An item is "done" only when ALL of these hold:
    - it is merged;
    - its flag is ON in production (or the owner has decided it stays OFF);
    - it is deployed;
    - its user journey is checked on the real site with screenshots;
    - the owner has given ✅.
- **R3. Phases in order.** Phase 2 does not start until Phase 1 is done, except for items marked "parallel".
- **R4. Status lives here.** The status table below is the only status list. README rows keep their history and point to the roadmap IDs. The session updates the table in every PR that changes a status.
- **R5. Token-saving way of working:**
  - At the start of a task: read CLAUDE.md, this file, and ONLY the section of the order the item points to. Never re-read whole order files or old reports.
  - Use short reports in this shape:
    - (1) "Roadmap: changed items" — ID, old → new status, one line each;
    - (2) what the owner must decide (max 3, each with a default);
    - (3) the deploy line + migration yes / no.
    - No step-by-step narration, no test-count lists, no repeated background.
  - Screenshots go in ONE comparison page per milestone, not per PR.
  - Questions are batched; each one has a default; the work continues.
  - Parallel agents write only to their own folders (no overwriting of shared results).
- **R6. Unfinished flags.** Every flag in flag_catalog.py marked "unfinished" gets one of three fates in P2-1: finish it (an item with a date order), keep it OFF and hidden (with the reason), or remove it from the user's view. No half-built feature stays visible to a user.

## The goal of ATP v1 (what "a working product" means)
A trader signs in on https://aialgodesk.tech and can do all of the following on PAPER, with real broker data, on desktop and phone, with nothing confusing on screen:
- **J1 Start:** sign in → connect his Upstox (own app) → Today opens with his ready-made information (market, his P&L, what needs him).
- **J2 Read the market:** charts (both-axis scroll, Reset), option chain with charts, watchlists (system + own).
- **J3 Get signals:** pick a ready strategy or template → signals arrive (screen + Telegram) → Approve → paper trade → Positions → exit → Journal → Performance.
- **J4 Trade by hand:** the Terminal (T1–T3): chart / chain / ticket, spot or premium exits, group exits, Exit all.
- **J5 Ask the Copilot:** one key pasted once → Copilot answers about his positions and risk (never places orders).
- **J6 Stay safe:** his own risk limits (OFF until set), kill switch, exits never blocked.
- **J7 Get help:** page tours, help centre, plain words everywhere.
- **J8 Run safely:** backup off-site, monitoring, safe deploys, privacy (DPDP) — the owner can sleep.
LIVE stays OFF (the owner's switch). Marketplace, public API, self-signup and payments come last (Phase 5).

## Phases and items

### Phase 1 — Finish what is in flight (now)
| ID | Item | Source | Done when |
|---|---|---|---|
| P1-1 | Colour v2 merged + ON | row 18 Part 3, #447 | merged, deployed, ✅ given (24 Oct) |
| P1-2 | Risk limits OFF by default (TT-12) | #494 | merged + deployed; the org min R:R handled in P1-2b |
| P1-2b | Org-level min R:R 1.2 OFF (no unstated rule), backtest parity | TT12-4 | merged with parity tests |
| P1-3 | Trader pack + day-one trader test (J1, J3) | row 19, #470 | the day-one test passes on production with screenshots; universe sync ON |
| P1-4 | One chart core + both-axis scroll / Auto / Reset / Latest (J2) | row 18 Parts 1–2 | every chart page uses the core; gesture tests; phone recording |
| P1-5 | Data hub ON (live prices everywhere) | row 20 Part 1 | flag `data_hub` ON in production; API-call numbers before / after |
| P1-6 | Terminal T1 → T2 → T3 (J4) | row 21 | `terminal` ON after the owner's ✅ on screenshots |
| P1-7 | Smart execution ON | row 20 Part 7 (#474 merged) | flag ON; basket confirm step checked on production |

### Phase 2 — Turn ON and prove what is already built (J3, J5, J7)
| ID | Item | Source | Done when |
|---|---|---|---|
| P2-1 | Flag audit: every "unfinished" flag → finish / keep OFF hidden / remove from view | flag_catalog.py, R6 | one table for the owner with a recommendation per flag; his answers applied |
| P2-2 | Copilot jobs 1–8 + own key + tools work end to end | row 5, row 6 | J5 checked on production |
| P2-3 | Strategy templates + idea library + wave templates usable from the Paper bot | rows 12, 13, 14 | a user adopts a template and gets signals (J3) |
| P2-4 | Daily rhythm (brief, pulse, wrap, weekly) ON | row 9 | Telegram + Dashboard card on production |
| P2-5 | Analyze a chart ON (beta) | row 10 | works with a vision key; strikes from his own settings only |
| P2-6 | Help centre + page tours complete | rows 6, 8 | J7 checked |
| P2-7 | Positions for options sellers + Performance page | row 20 Parts 2, 4 | J3 end (Performance) checked |

### Phase 3 — Production readiness (J8) — starts in PARALLEL with Phase 2
| ID | Item | Source | Done when |
|---|---|---|---|
| P3-1 | Off-site backup (R2) + restore drill | row 17 A1 | a restore was tested; owner task: the R2 bucket |
| P3-2 | Monitoring + alerts + health | row 17 | the owner gets an alert when the site or a worker is down |
| P3-3 | Safe deploys + rollback | row 17 | `atp.sh` rollback tested |
| P3-4 | Security + DPDP privacy | row 17 | checklist done; the lawyer items listed |
| P3-5 | Legal pages + SEBI groundwork | row 17, COMPLIANCE_IN.md | text from the lawyer (owner task) |

### Phase 4 — The advanced terminal
| ID | Item | Source |
|---|---|---|
| P4-1 | Terminal T5 + T6 (algo panel, discipline tools) | row 21 |
| P4-2 | Terminal T4 (option-seller tools) + options what-if / stress test | row 21, row 20 Part 9.1–9.2 |
| P4-3 | Kotak Neo + Groww adapters (merged, live gated) → connect guides checked | row 20 Part 1B |
| P4-4 | Strategy control bar + profit ladder | row 20 Part 5 |
| P4-5 | Volatility / OI / participants analytics; chain history in use | row 20 Part 9.3–9.5 |
| P4-6 | GTT / OCO / ATO / iceberg / SIP | row 20 Part 7 |
| P4-7 | Sector heatmap | row 20 Part 3 |
| P4-8 | Market replay with the reader overlay + Terminal T7 | row 20 Part 10, row 21 |
| P4-9 | Position helpers (roll what-if) + AI risk questions | row 20 Parts 11–12 |

### Phase 5 — Business launch (last; owner decisions)
| ID | Item |
|---|---|
| P5-1 | Chart reader port from Trade — only after the Trade reader passes the owner's answer key |
| P5-2 | Payments (Razorpay), plans, Journal as a paid plan |
| P5-3 | Self-signup / invite cohort ON; Google sign-in (owner's OAuth credentials) |
| P5-4 | Mobile app (Play Console) + push |
| P5-5 | Marketplace + public API (the last phase, about six months out) |

## Status table (the session keeps this current; one line per item)
| ID | Status | Next action | PR |
|---|---|---|---|
| UI-H | in progress | audit approved (#503); Wave 0 shared components next (one SubTabs style; mocks per wave), then Wave 1 | #503 |
| UI-H-BUG | in progress | bug lane: C1, B2a/B2b, B3+B4+Orders time, A1, B1 - small PRs, each with proof | - |
| P1-1 | done (✅ 24 Oct) | owner deploys; check on production | #447 |
| P1-2 | in review | notice (TT12-1) added; owner: "Merge" after green CI, then deploy (no migration) | #494 |
| P1-2b | in review | org min R:R OFF (0 = not set; an old stored 1.2 = not set), parity tests live / paper / both backtests; owner: "Merge" after green CI, then deploy (no migration) | #497 |
| P1-3 | waiting for owner | deploy, universe sync ON, "Enable trader features"; then day-one test on production | #470 |
| P1-4 | in progress | verify which chart pages still bypass the core; gestures; phone recording | row 18 |
| P1-5 | waiting for owner | `data_hub` ON via "Enable trader features" after the deploy; API-call numbers before / after | - |
| P1-6 | in progress | T1 waits for ✅; TT-2 (#492) and TT-8 (#501) merged; TM-2 in review (#496, migration); T2 then needs TT-1; partial exit = TT-9 | #493, #496 |
| P1-7 | waiting for owner | `smart_execution` ON ("Enable trader features"); check the basket confirm on production | #468, #474 |
| P2-1 | not started | next free P1 slot: one flag-audit table with a recommendation per flag | - |
| P2-2 | not started | Copilot jobs 1-8 + own key + tools end to end (parts built, flags) | - |
| P2-3 | not started | templates / ideas / wave templates from the Paper bot (parts built) | - |
| P2-4 | not started | daily rhythm ON (built, flag) | - |
| P2-5 | not started | Analyze a chart ON (beta) | - |
| P2-6 | not started | help centre + page tours complete | - |
| P2-7 | not started | positions for options sellers + Performance page | - |
| P3-1 | waiting for owner | the R2 bucket + key escrow, then the restore drill | - |
| P3-2 | not started (parallel) | monitoring + alerts (parts built, row 17) | - |
| P3-3 | not started (parallel) | rollback test of `atp.sh` | - |
| P3-4 | not started (parallel) | security + DPDP checklist | - |
| P3-5 | waiting for owner | the lawyer's text | - |
| P4-1 | not started | Terminal T5 + T6 | - |
| P4-2 | not started | Terminal T4 + what-if / stress test | - |
| P4-3 | in review | Kotak Neo / Groww merged with LIVE gated; connect guides to check | #473, #481 (merged) |
| P4-4 | not started | control bar + profit ladder | - |
| P4-5 | not started | chain history merged (#465, #471); analytics on it | - |
| P4-6 | not started | GTT / OCO / ATO / iceberg / SIP (needs TM-2) | - |
| P4-7 | not started | sector heatmap | - |
| P4-8 | not started | replay + T7 | - |
| P4-9 | not started | position helpers + AI risk questions | - |
| P5-1 .. P5-5 | not started | last phase (owner decisions) | - |

Statuses: not started / in progress / in review / waiting for owner / done. Max 3 build items "in progress" (R2):
now P1-2b, P1-4, P1-6.

## Owner tasks (shown in every report until done)
- R2 bucket + key escrow; Telegram bot / SMTP; company registration; lawyer (legal pages, SEBI / static-IP list); Razorpay; Firebase / Play Console; WhatsApp number; Google OAuth credentials.
