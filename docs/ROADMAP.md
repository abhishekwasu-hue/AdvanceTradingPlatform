From Abhi — THE ATP ROADMAP: one common file for the whole project (English). Save it as docs/ROADMAP.md and keep it current.

## Why this file exists
- ATP has 21 owner orders (README rows 0–21). Most of them are "partial", and about 20 features sit behind flags that are OFF or "unfinished".
- Work has been spread over too many items at once. The owner cannot see one finished product, and tokens are spent re-reading long orders and long reports.
- This file is the ONE plan. It does not replace or rewrite any owner order: it puts them in order, defines "done", and limits how much runs at once.

## Rules (binding; add them to DECISIONS)
- **R1. One plan.** Every task belongs to a roadmap item below (an ID such as P1-3). A new owner instruction is placed into this file (a new item or a change to one), in the same turn it is recorded in INBOX. Nothing is built outside the roadmap.
- **R2. Finish before start.**
  - At most 5 build items "in progress" at a time, over all sessions (owner 26 Oct; was 3) (plus fixes the owner asks for).
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
| P1-6c | CX Combined stop-loss / target on positions (Terminal + My trades) | ATP_ORDER_CX_SX.md | CX1-CX7 tests pass; phone + desktop screenshots; owner ✅ |
| P1-6d | SX Strategy-level SL / target / trailing at entry (spread + basket tickets), same engine as CX | ATP_ORDER_CX_SX.md | SX1-SX8 tests pass; ticket + chip screenshots; owner ✅ |
| P1-7 | Smart execution ON | row 20 Part 7 (#474 merged) | flag ON; basket confirm step checked on production |
| P1-8 | WL1 Watchlists (J2): ready-made system lists (Indices, Index stocks, Sectors, F&O) + own lists, live prices, phone layout | handover item 4; INBOX 24 Oct | `watchlists` ON in production, the lists filled by the universe sync, a Watchlist entry on the phone bar, live prices checked on production; owner ✅ on screenshots |
| P1-B1 | BUG: chain shows contracts the ticket / basket cannot trade ("not in the instrument list yet") - master sync retried with back-off + start-up catch-up, on-demand refresh, honest text, IST expiry dates, next-expiry default on expiry days (setting), exits never need the master | INBOX 27 Oct | the same ticket and basket place on production on an expiry day; before / after phone screenshots; owner ✅ |
| SCR-FIX | Screener audit + fix pack: A data path (non-native intervals resampled, daily window, today's bar, last bucket, session budget, index volume, primary credential), B truthful results, C real chart + Telegram in place, D phone UI, E text + guards | ATP_ORDER_SCR_FIX.md | each item verified then fixed with a test; PROOF comparison page; owner ✅ |

### Phase P2-F — Every feature 100% functional on production with real broker data (owner 25 Oct; before Phase 2 items)
Order: FN0 first (audit, no code), then FN1 -> FN7 as listed; max 3 in progress; one PR per item. Source: ATP_ORDER_P2F_FUNCTIONAL.md.
| ID | Item | Source | Done when |
|---|---|---|---|
| FN0 | Functional audit: every user-facing feature + flag -> works / partial / broken / hidden-unfinished, evidence, fix item (merged with the P2-1 flag audit) | ATP_ORDER_P2F_FUNCTIONAL.md | one table for the owner; nothing half-built visible (R6) |
| FN1 | Definition of done for every feature (e2e main journey, production with own broker data phone + desktop, (?) help, flag ON or removed, logs + alerts, no sev-1/2, plain errors) | same | written into WORKING_RULES + a per-feature checklist used by FN7 |
| FN2 | Indicator screener (Chartink-class: indicators, F&O OI/PCR, 1m-monthly, operators, crosses, n bars ago, AND/OR, multi-TF, math, universes, schedule, results actions, library, alerts, scan history) | same | FN1 checklist ticked on production |
| FN3 | Custom indicators: formula builder, validation, chart preview, personal library, usable in screeners + strategies, safe sandbox with limits | same | FN1 checklist ticked on production |
| FN4 | Indicator-based algo strategies: builder, options strikes + spreads, size from settings, SX exits, time windows as settings, ONE engine backtest = paper = live (parity), costs, metrics, forward test, logs, parallel, kill switches; LIVE behind owner's switch | same | FN1 checklist + parity test on production |
| FN5 | AI connectivity + AI-assisted decisions: own key + test connection, Copilot jobs end to end, plain words -> builder (review + save, never auto-run), signal explanation, AI call log + limits, clear errors; AI never touches orders | same | FN1 checklist ticked on production |
| FN6 | Live-automation compliance note (SEBI retail algo framework): what ATP does / misses / owner's steps (owner + lawyer before LIVE) | same | note delivered to the owner |
| FN7 | Proof per item: production screenshots phone + desktop with real data, short recording, e2e test name, FN1 checklist; chart reader hidden with an honest status until P5-1 | same | one proof page per item, owner ✅ |

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
| P2-8 | Journal v2 (J1-J6): broker sync, chart image, spread view, plan vs actual; 20-second check-in; daily / weekly / monthly reviews; My proof; if-then plans + process goals; Telegram / e-mail; page tabs (extends app/journal + discipline_engine) | ATP_SESSION_JOURNAL.md | 3 PRs with tests + screenshots; owner ✅ on each; billing in P5-2 |

### Phase 3 — Production readiness (J8) — starts in PARALLEL with Phase 2
| ID | Item | Source | Done when |
|---|---|---|---|
| P3-1 | Off-site backup (R2) + restore drill | row 17 A1 | a restore was tested; owner task: the R2 bucket |
| P3-2 | Monitoring + alerts + health | row 17 | the owner gets an alert when the site or a worker is down |
| P3-3 | Safe deploys + rollback | row 17 | `atp.sh` rollback tested |
| P3-4 | Security + DPDP privacy | row 17 | checklist done; the lawyer items listed |
| P3-5 | Legal pages + SEBI groundwork | row 17, COMPLIANCE_IN.md | text from the lawyer (owner task) |
| TP-1 | F1 Pre-trade risk card on every order review (single, spread, basket) | ATP_ORDER_TRADER_PROTECTION.md | merged + flag decided + deployed + checked on production; phone + desktop screenshots; owner ✅ |
| TP-2 | F7 Naked-short warning (review + positions; warning only) | ATP_ORDER_TRADER_PROTECTION.md | merged + flag decided + deployed + checked on production; phone + desktop screenshots; owner ✅ |
| TP-3 | F3 Charges & slippage meter (per trade, per month) | ATP_ORDER_TRADER_PROTECTION.md | merged + flag decided + deployed + checked on production; phone + desktop screenshots; owner ✅ |
| TP-4 | F2 Personal rules guard (user-set, all OFF by default; exits always allowed) | ATP_ORDER_TRADER_PROTECTION.md | merged + flag decided + deployed + checked on production; phone + desktop screenshots; owner ✅ |
| TP-5 | F5 Alerts: zone entry, false / confirmed break, combined-SL distance (screen + Telegram / WhatsApp, confirm step) | ATP_ORDER_TRADER_PROTECTION.md | merged + flag decided + deployed + checked on production; phone + desktop screenshots; owner ✅ |
| TP-6 | F4 Auto journal + weekly review report | ATP_ORDER_TRADER_PROTECTION.md | merged + flag decided + deployed + checked on production; phone + desktop screenshots; owner ✅ |
| TP-7 | F6 Option-seller tools: what-if / stress, roll helper, adjustment information | ATP_ORDER_TRADER_PROTECTION.md | merged + flag decided + deployed + checked on production; phone + desktop screenshots; owner ✅ |
| TP-8 | F8 Copilot post-trade review from the journal (uses CP-V) | ATP_ORDER_TRADER_PROTECTION.md | merged + flag decided + deployed + checked on production; phone + desktop screenshots; owner ✅ |

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
| UI-H | in progress | Wave 0 fixes ✅ (25 Oct), #510 merging; page waves carry H9 (phone titles never truncated, e.g. "Sectors & ind…") and W0-4 (ISO dates -> "27 Oct 2026"); then Wave 1 | #503, #510 |
| UI-H-BUG | in progress | bug lane: C1 (merged #506: history ends at the last session close, empty state with its reason, `atp.sh probe-candles`), B2a/B2b (merged #507: "as of" the last session close, "Spot: last close", `atp.sh probe-chain`), B3+B4+Orders time (merged #508), A1 (merged #505), B1 (#509: own-broker chain reads = the user's session snapshots; Analyses / Charts / OI reads no longer refused) - small PRs, each with proof | C1: #506; B2: #507; B3/B4: #508; A1: #505; B1: #509 |
| UI-H-CA | in progress | CA1-CA8 ✅ (25 Oct); fix CA6-1 distinct bottom-nav hues (Today blue, Charts violet, Signals amber, My trades pink, More teal; AA + colour-blind check), Option chain spot = chart feed check; then merge #534 | #534 |
| UI-H-DM | in progress | dark-mode contrast DM1-DM5 in colour_v2 dark tokens (surfaces, borders >= 3:1, icons/labels >= 4.5:1, primary + active >= 7:1, Appearance "High contrast", ratio test); stacked on #534; phone comparison page (dark, normal + high contrast) | - |
| UX-2 | in progress | Session B, plan docs/design/UX2_PLAN.md: merged H9 #559, UX-6 #560, UX-2 search #563, UX-5 #564, UX-7, UX-4, TD-1 #577, OC-1/2 #579, Orders #581, Charts H7 #582, UX-9 ticket #583; UX-8 density #578, OC-4 #593 merged; next OC-5; PR 8 (Terminal watchlist) waits on Session A T2/T3; owner deploys + checks | #577-#583 |
| UX-11 | in progress | Session B: "Numbers first" (ATP_ORDER_UX11.md): merged #584 #585 #586 #587 #589 (incl. #588); #594 (Option chain, Signals, Paper bot, Risk, Ideas, Setup, AI keys, Help + guard on 25 pages) on CI; then owner deploy + check | #584-#594 |
| UX-12 | in progress | Session B: purple dot-grid backdrop behind the v2 page header band (owner screenshot, 27 Oct), Appearance toggle, default on; owner OK on the sample 27 Oct | - |
| UX-13 | in progress | Session B: bottom-bar option C (owner, 27 Oct): colour on the icon only, the round fill only on the tab on screen; lighter tile grid lines; behind ui_v2 | - |
| UI-H-TF | in progress | TF-1 one timeframe control everywhere (grouped dropdown + custom interval + starred favourites; phone dropdown + 3 favourites); shared component replaces TimeframeTabs | - |
| SB-1 | not started | desktop sidebar show / hide button (collapsed = icon rail), remembered per user; after the limit resets | - |
| DATA-H | in progress | market-lake history backfill + daily append + auction-close rule + health + honest "history loading" (DH1-DH6) | - |
| CP-V | in progress | review done twice (CP_V_REVIEW.md 24 Oct, CP_V_SPEC.md 26 Oct); fixed spec ✅ 26 Oct (CPV-15..17 defaults); build after DATA-H | #555 |
| P1-1 | done (✅ 24 Oct) | owner deploys; check on production | #447 |
| P1-2 | in review | notice (TT12-1) added; owner: "Merge" after green CI, then deploy (no migration) | #494 |
| P1-2b | in review | org min R:R OFF (0 = not set; an old stored 1.2 = not set), parity tests live / paper / both backtests; owner: "Merge" after green CI, then deploy (no migration) | #497 |
| P1-3 | waiting for owner | deploy, universe sync ON, "Enable trader features"; then day-one test on production | #470 |
| P1-4 | in progress | verify which chart pages still bypass the core; gestures; phone recording | row 18 |
| P1-5 | waiting for owner | `data_hub` ON via "Enable trader features" after the deploy; API-call numbers before / after | - |
| P1-6 | in progress | TF-1 one timeframe control #540 merged 26 Oct. T1 + fixes ✅ (25 Oct): #510 + #493 merged; `terminal` ON by default merged (#533, T-D1), live after the deploy; the two unbuilt panels hidden (#539). Owner TL-1..TL-4 (25 Oct: chart panel never navigates; 45/35/20 layout + gutters; compact chain + column picker; compact order panel) with T2. T2 target 29 Oct: TT-1 LIVE sender (own PR; LIVE switch stays the owner's), TT-9 partial exit + reverse, TT-10 modify / cancel (PAPER). T3 target 2 Nov | #493 |
| P1-6c | in review | PR 1-3 merged (#524, #527, #529, #553); CX2 trailing (LOCK / TRAIL) merged via #603 27 Oct with the owner review F2-F7 (stop in force = engine state, signed P&L, shared option cards, one-line dialogs); owner ✅ on shots with fixes; waits for deploy + owner check | #601, #603 |
| P1-6d | in review | SX: Exits for this strategy on the basket ticket, LOCK_TRAIL / BREAK_EVEN, one combined exit (origin SX) at the fill; merged #603 27 Oct (SXQ-1..4 in OPEN_QUESTIONS); waits for deploy + owner check | #603 |
| P1-8 | in progress | 26 Oct: W-PR #554 merged (owner ✅): Watchlist page + phone bar tab after Charts, behind `ui_v2` + `watchlists`. On main: server lists + Charts watchlist panel (chooser, search, sort, copy to my list, live ticks with `data_hub`). Missing: `watchlists` flag OFF in production; lists empty until the universe sync runs (P1-3); no Watchlist entry of its own on the phone bar (only inside Charts). Plan: (1) owner turns on via "Enable trader features" + System tab refresh; (2) W-PR: Watchlist page / phone bar entry using the same panel, behind ui_v2; (3) screenshots for ✅ | - |
| P1-B1 | in review | merged #575 + #595; deployed 27 Oct 21:00 IST (a5db3e1), instrument master ok (91,037 rows, no error); owner check 28 Oct 09:15: a PAPER ticket on a NIFTY contract | #575, #595 |
| SCR-FIX | in progress | owner order 27 Oct (blocks the day-one test): re-verification of every item first, then PRs A -> E | - |
| P1-7 | waiting for owner | `smart_execution` ON ("Enable trader features"); check the basket confirm on production | #468, #474 |
| FN0 | in progress | functional audit (no code), merged with P2-1: feature + flag table with status, evidence and fix item | - |
| FN1-FN7 | not started | after FN0, in order; max 3 in progress | - |
| P2-1 | in progress | table sent (docs/design/P2_1_FLAG_AUDIT.md: 26 unfinished flags, finish 10 / keep OFF 15 / remove 1); owner ✅ 26 Oct (FA-1..3 defaults); next: apply in the catalog / admin list (build PR) | #556 |
| P2-2 | not started | Copilot jobs 1-8 + own key + tools end to end (parts built, flags) | - |
| P2-3 | not started | templates / ideas / wave templates from the Paper bot (parts built) | - |
| P2-4 | not started | daily rhythm ON (built, flag) | - |
| P2-5 | not started | Analyze a chart ON (beta) | - |
| P2-6 | not started | help centre + page tours complete | - |
| P2-7 | not started | positions for options sellers + Performance page | - |
| P2-8 | not started | Journal v2 J1 -> J2+J3 -> J4+J5+J6 (3 draft PRs); starts when the max-3 rule allows | - |
| M45 | in progress | MA-1a #568, MA-1b #569, MA-2 #571 merged; MA-3/4/5 PAPER fan-out, one exit, checks + alerts + retry (migration f1b3d5e7a9c2) #576; MA-6 ticket "Run on" + practice accounts page in review; then flag decision + deploy (owner's one command); LIVE fan-out only on the owner's LIVE words | MA-6 PR |
| P3-1 | waiting for owner | the R2 bucket + key escrow, then the restore drill | - |
| P3-2 | not started (parallel) | monitoring + alerts (parts built, row 17) | - |
| P3-3 | not started (parallel) | rollback test of `atp.sh` | - |
| P3-4 | not started (parallel) | security + DPDP checklist | - |
| P3-5 | waiting for owner | the lawyer's text | - |
| TP-1 | not started | starts only after Phase 1 is complete | - |
| TP-2 | not started | after TP-1 (in order) | - |
| TP-3 | not started | after TP-2 (in order) | - |
| TP-4 | not started | after TP-3 (in order) | - |
| TP-5 | not started | after TP-4 (in order) | - |
| TP-6 | not started | after TP-5 (in order) | - |
| TP-7 | not started | after TP-6 (in order) | - |
| TP-8 | not started | after TP-7 (in order) | - |
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

Statuses: not started / in progress / in review / waiting for owner / done. Max 5 build items "in progress" (R2, owner 26 Oct; was 3):
now P1-2b, P1-4, P1-6.

## Owner tasks (shown in every report until done)
- R2 bucket + key escrow; Telegram bot / SMTP; company registration; lawyer (legal pages, SEBI / static-IP list); Razorpay; Firebase / Play Console; WhatsApp number; Google OAuth credentials.
