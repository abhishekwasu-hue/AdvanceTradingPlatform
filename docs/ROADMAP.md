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
| DEF-0 | No platform defaults in order placement / risk: D1 RiskConfig all optional, D2 hard-coded capital, D3 hidden exits (AP-FIX AP7), D4 experience levels a user opt-in, D5 override multipliers, D6 ticket truth, D7 stop-loss source, D8 margin estimate, D9 My limits card, D10 migration (extends TT-12; merges AP-FIX AP4 / AP7) | ATP_ORDER_DEF0.md | first report per D-item; tests (no-settings user can place / Autopilot can enter; each setting applies; exits never blocked; PAPER = LIVE); CI grep guard; PROOF page; owner ✅ |
| UI-STD | UI standards audit fix pack: A1-A8 remove what no mainstream tool shows (+ user-string lint), B1-B13 standards floor (NN/g, WCAG 2.2 AA, Material, HIG); extends UX-11 / UX-11b / UX-14 / SCR-FIX | ATP_ORDER_UI_STD.md, UI_STANDARDS_AUDIT.md | first report per item; PR 1 = A, PR 2 = B1-B4, PR 3 = B5-B13; before / after page per PR; owner ✅ |
| UI-28 | Consolidated UI order (owner 28 Oct; supersedes UI-STD / UI-STD-C and the UI parts of UX-11, UX-11b, UX-14, Screener UI, Telegram in place, OI view, PX-1 P5/P6): U1 remove what no mainstream tool shows, U2 numbers first + logical colour, U3 standards floor, U4 palette, U5 Screener + chart + Telegram UI, U6 OI view in v2, U7 beyond the best; UI only | ATP_ORDER_UI_28OCT.md, UI_STANDARDS_AUDIT.md | first report per item; one PR per U group in order; comparison page per PR; final summary page with word counts + contrast table; owner ✅ |
| ADM-1 | Admin console in category groups (owner 30 Oct "too many controls, very confusing"): Controls split into groups, feature flags in finer folded categories with a status filter; UI only, nothing removed | INBOX 30 Oct | before / after page; owner ✅ |
| AP-FIX | Autopilot fix pack (owner 28 Oct; full text to be filed from the owner's message) - AP1 = AUD-V1; AP4 / AP7 merged into DEF-0 | ATP_ORDER_AP_FIX.md (stub) | as its order says |
| CP-FIX | Copilot fix pack (owner 28 Oct; full text to be filed from the owner's message) | ATP_ORDER_CP_FIX.md (stub) | as its order says |
| OI-V2 | OI banner v2 (owner 28 Oct; full text to be filed) - the regime hysteresis gap is AUD-V5 | ATP_ORDER_OI_V2.md (stub) | as its order says |
| PX-1 | Prices everywhere / after hours (owner 28 Oct; full text to be filed) - P1 = AUD-V2 | ATP_ORDER_PX_1.md (stub) | as its order says |

### Phase AUD — Complete audit: verify -> grade -> differentiate (owner 28 Oct)
Order: V -> G1 -> T1-T3 -> G2 -> T4+; max 5 in progress; one PR per group. Source: ATP_ORDER_AUDIT_28OCT.md; evidence: docs/design/AUDIT_28OCT.md.
| ID | Item | Source | Done when |
|---|---|---|---|
| AUD-V1 | Autopilot decides on closed bars only (trim the forming bar) + parity test backtest == worker (= AP-FIX AP1) | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-V2 | Open P&L after hours from the stored mark ("last mark HH:MM"), counted as priced (= PX-1 P1) | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-V3 | VIX bands from the one shared table (ui/levels.ts vixLevel + LevelChip) | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-V4 | Discipline score: "n/a until a trade closes" at 0 closed; else "(N open · M closed)" | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-V5 | OI regime: structural flip (3 negative delta-diff + call OI up >= 2 %); OI_BANNER.md; owner's numbers as the test; Trade check | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-V6 | Text-budget guard reads leaf div / span text >= 15 words in cards | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-V7 | Repo hygiene: delete merged branches (+ on merge), semver tags, CHANGELOG.md + in-app What's new | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-G1.1 | DPDP: /api/me/export + /api/me/erase (grace, confirm, audit) + Settings -> Account | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-G1.2 | Legal placeholders (owner supplies fiduciary + grievance officer), 90-day SLA, runbooks/BREACH.md | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-G1.3 | Production: no /docs /openapi.json /redoc (or SUPER_ADMIN only); checked in the deploy health step | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-G1.4 | Browser error tracking (@sentry/react, PII scrubbed); email / phone redaction in JSON logs | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-G1.5 | Core Web Vitals: Lighthouse CI budgets + web-vitals RUM to /metrics | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-G1.6 | Dependabot / Renovate, ESLint + typescript-eslint, ruff I / UP / ASYNC, mypy on all of app | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-G1.7 | SEBI retail-algo readiness: owner Compliance page + signals-not-advice / RA boundary text (lawyer) | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-G2.1 | OpenTelemetry tracing + trace id in logs; N+1 profile of the top 5 list endpoints | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-G2.2 | Live prices end to end: broker WS -> SSE; remove the 60 s pollers; tick-age stale chip | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-G2.3 | Self-hosted fonts, strict CSP, preload; maskable icon + screenshots; iOS later | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-G2.4 | Activation metrics atp_activation_total{step} + admin funnel | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-G2.5 | Signed outbound webhooks + WhatsApp Business (owner: number) | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-T1 | My day in one card (position-aware brief) | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-T2 | Expiry-day & margin checklist (T-1 session) | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-T3 | Why no trade today timeline (BR-S1 log) | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-T4 | Paper report card (forward test) | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-T5 | Read this chart (Trade reader as a service) | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-T6 | Similar-setup recall + what the context said vs what happened (BR-M3 / BR-R1) | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-T7 | Cost-of-trading meter | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-T8 | Reliability promise: public status page, degradation banner, published SLOs | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |
| AUD-T9 | Pricing (owner decision; defaults in the order; lawyer check before charging) | ATP_ORDER_AUDIT_28OCT.md | first report (confirmed / not reproduced) in docs/design/AUDIT_28OCT.md; tests; before / after proof page; owner ✅ |

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
| UI-STD-C | UI-STD group C, beyond the best (C1-C7, UI only, behind flags; C8 stays UX-11b), one PR each | ATP_ORDER_UI_STD.md | each item's complaint link + screenshots; owner ✅ |

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

### Phase BR — Trading Brain (owner 28 Oct; starts only after BR-0)
| ID | Item | Source | Done when |
|---|---|---|---|
| BR-0 | Foundation gate: SCR-FIX, AP-FIX, CP-FIX, DATA-H backfill, P5-1 reader port passing its answer key - merged + deployed + checked | ATP_ORDER_TRADING_BRAIN.md | every listed item done; then the BR review (<= 25 lines) gets the owner's ✅ |
| BR-1 | Memory: M1 one shared reader, M2 episodes (extends journal capture J1), M3 similar-setup recall, M4 user method profile, M5 chat working memory | ATP_ORDER_TRADING_BRAIN.md | parity / isolation / no-look-ahead tests; recall examples with charts; owner ✅ |
| BR-2 | Reflection + calibration: R1 post-trade line, R2 weekly lessons as proposals, R3 reliability chart + Brier, R4 "I don't know" (>= 20 closed PAPER episodes) | ATP_ORDER_TRADING_BRAIN.md | reliability chart; proposals never auto-applied; owner ✅ |
| BR-3 | Second opinion + state + proactive: S1 setup state machine (parity), S2 pre-trade check, S3 for / against, S4 brief + alerts | ATP_ORDER_TRADING_BRAIN.md | byte-identical decisions on stored history; with / without comparison; owner ✅ |

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
| UX-11b | merged - deploy + owner check | Session B: "Numbers first, logical colour" whole-app pass (ATP_ORDER_UX11B.md): C1 + C6 #622, C2 Today #623, C3 dishes #624, C4 + C5 #625, C7 + C8 #626 all merged 28 Oct (no migration, no new flag); next: deploy, owner checks on the site | - |
| UX-12 | done | Session B: purple dot-grid backdrop behind the v2 page header band, Appearance toggle, default on; merged #609, deployed 77a50057db57, owner OK 28 Oct | #609 |
| UX-13 | done | Session B: bottom-bar option C (colour on the icon, round fill only on the tab on screen), lighter tile grid lines; merged #609, deployed 77a50057db57, owner OK 28 Oct | #609 |
| UX-14 | in progress | Session B: "Professional palette" (ATP_ORDER_UX14.md): colour_v3 token set behind its flag, CI contrast for every pair, ONE comparison page (Today, Terminal, Screener, My trades, Signals; 390 + desktop, dark + light); owner 28 Oct: ON by default ("Keep Default on always"), the flag kept to turn it off (Admin -> Features) | - |
| UX-15 | merged - deploy + owner check | Session B: Positions tab to the broker standard (owner 28 Oct), #639: standard columns and definitions, total P&L first, one amber chip for a missing price / stop, no theory text on screen; Terminal + My trades | - |
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
| SCR-FIX | in review | A-E merged 28 Oct (#614, #619, #618, #613, #620); PROOF page + deploy next | - |
| DEF-0 | merged - deploy + owner ✅ | PR 1-4 + fix #641 merged; deploy line ece32fd8 / e2f099f2 sent (migration yes: d4 d5 d6); owner ✅ on PROOF | - |
| UI-STD | superseded by UI-28 | Session B: owner order 28 Oct filed; docs/design/UI_STANDARDS_AUDIT.md written; first report (confirmed / not reproduced) next, then PR 1 = A1-A8 | - |
| UI-STD-C | superseded by UI-28 (U7) | - | - |
| UI-28 | built, awaiting deploy + owner ✅ | Session B: U1 #640, U2 + U1.10 / U1.11 #647, U3 #650 #653 #675 #676 #679, U4 palette #681 + Today numbers first #683, U5 #686, U6 OI view #689, U7.1 #690, U7.3 #698, U7.4 #692, U7.5 #694, U7.7 #697 merged (U7.2 already covered by StrategyExitsV2, U7.6 earlier); SEBI text parked (UI28Q-1 on hold); UI28Q-2 decided: organisation-wide (owner "Default yes"); final before / after page sent 29 Oct | owner ✅ on the before / after page |
| ADM-1 | in review | #711 | owner ✅ on the before / after page |
| AP-FIX | merged - deploy + owner ✅ | text lost (DECISIONS 29 Oct): AP1 = AUD-V1 #643, AP4 / AP7 in DEF-0 | - |
| CP-FIX | waiting for owner | text lost and no item quoted anywhere; out of the BR-0 gate until restated (DECISIONS 29 Oct) | - |
| OI-V2 | merged - deploy + owner ✅ | text lost (DECISIONS 29 Oct): the regime flip = AUD-V5 #648 | - |
| PX-1 | merged - deploy + owner ✅ | text lost (DECISIONS 29 Oct): P1 = AUD-V2 #644 | - |
| AUD-V1..V7 | merged - deploy + owner ✅ | V1 #643, V2 #644, V3 #645, V4 #646, V5 #648, V6 #649, V7 #651 merged 28 Oct; in e2f099f2 (deploy line sent); v1.0.0 tag + branch prune need the owner (proxy 403) | - |
| AUD-G1.1..G1.7 | in progress | G1.1 #654, G1.3 #652, G1.4 part 1 #655, G1.5 #670, G1.6 part 1 #656, G1.7 #657 #671 merged; G1.2 waits for the owner's company / grievance details; G1.4 part 2 waits for a Sentry DSN; G1.6 part 2 #708 (ruff ASYNC + I, UP + ESLint ratchets, mypy +3) on CI | #708 |
| AUD-T1..T3 | merged - deploy + owner ✅ | T3 #673 (flag `why_no_trade`, migration d0f0a1b2c3d7), T1 #687 (`my_day_card`), T2 #685 (`expiry_checklist`; owner to read the 4 broker notes); all flags OFF | - |
| AUD-G2.1..G2.5 | merged - deploy + owner ✅ (WhatsApp waits) | G2.1 #693, G2.3 #700, G2.4 #696 (migration d0f0a1b2g204), G2.5 webhooks #701 (`alert_webhooks`) merged; G2.2 live push #702 (`live_push`) merged; WhatsApp waits for the owner's number | - |
| AUD-T4..T9 | in progress | T4 #695 (`paper_report_card`), T6 #703 (`setup_recall`), T7 #688 (`cost_meter`), T8 #691 (`status_page`, migration d0f0a1b2c3d8), T5 #707 (`read_chart`; full reader lifecycle waits for P5-1) merged; T9 pricing waits for the owner | - |
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
| BR-0 | waiting | gate only: SCR-FIX (in progress), AP-FIX, CP-FIX, DATA-H backfill, P5-1; BR filed 28 Oct, plan only | - |
| BR-1 | not started | after BR-0 + the owner's ✅ on the BR review | - |
| BR-2 | not started | after BR-1 | - |
| BR-3 | not started | after BR-2 | - |
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
