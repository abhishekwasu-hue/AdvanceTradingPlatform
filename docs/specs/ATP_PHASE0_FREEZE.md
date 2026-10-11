From Abhi — Phase 0 scope freeze (English). Overrides the priority order in earlier messages until Phase 0 ships. Same working rules (never stop; OPEN_QUESTIONS with provisional defaults; multitask; self-review; independent review for engine/data/security PRs; WIP ≤ 10; merge your own PRs when CI is green and reviews pass; flags off by default; no VPS/LIVE/secret actions).

1. Phase 0 definition (the only target now)
 Invite-only beta, 300–500 users, PWA (website install), PAPER trading only, no API orders from ATP. A user can: sign up with an invite → link Upstox or Dhan (Tier A per-user live feed; others execution-only/EOD) → use screeners (EOD for everyone, live on their own feed) with alerts → open evidence charts → build an options strategy → receive proposals on Telegram/app → approve PAPER → see PAPER positions, protective exits, journal and discipline score → optional BYOK Vision. Market-wide pages run on EOD data.

2. IN scope — build in this order (each item: design note if new, PRs, tests, docs)
 Z1 PW5 proposal card + PW6 invite gate/feedback (finish).
 Z2 BT1 Tier A: per-user broker session fan-in (Upstox, Dhan), entitlement/isolation guard, daily-login reminder, data-tier badges, BROKER_DATA_TERMS.md with broker status gating.
 Z3 BT2/BT3 Phase 0 scope: proposal → Telegram/in-app/push → Approve PAPER; per-user PAPER account; protective exits automatic, others "Exit now?"; approval expiry/re-quote; progression ladder card (PAPER only).
 Z4 HB0 (#164) + HB1 daily 10-year backfill + nightly EOD jobs (shared lake), owner intraday store tenant-scoped.
 Z5 P1-c builder page (payoff canvas, draggable strikes, metrics) + P1-d minimal: plan-before-trade ticket for PAPER and risk-by-default caps/lockouts (sizing, daily loss lockout with exits allowed, cost line).
 Z6 P1-e journal auto-capture + behaviour tags + discipline score + weekly review (numbers from data; Copilot explains, no advice wording).
 Z7 U6 BK1 + BK2 breakout screens (range/swing/NR7/52-week/ATR/volume; ORB/first-hour/new HOD) with the quality overlay, and U8 EV1 + EV2 evidence charts (minimal: only the screen's conditions; verify strip; Telegram images).
 Z8 U9 TX1 taxonomy model + gallery rail/chips (method × setup) for the screens that exist; U7 LG1 first 8 Legends cards only if Z1–Z7 are done.
 Z9 BYOK-V2 (Vision audit on proposals with the user's own key, report format, caps).
 Z10 Launch hardening (U3 subset): G1 correctness for shipped indicators/patterns, G2 no-look-ahead harness, G4 perf targets for EOD/bar-close, G5 stale-data suppression + outbox reliability, G6 tenancy/security tests, G7 status page + runbooks; load test 500 concurrent sessions; staged rollout flags (50 → 200 → 500); DPDP/terms/risk-disclosure pages; support bot.
 Z11 Deploy bundle for Hostinger: one consolidated owner-run command (bootstrap → deploy → health check), rollback command, backup to R2 verified; the session never runs it.

3. AFTER Z1–Z11 are merged — continue immediately, no pause, no waiting for the owner or for the beta to go live (owner decision). Phase 0 is a priority order, not a stop. As soon as Z11 merges, start the list below in this order, with the same rules (WIP ≤ 10, reviews, flags off, merge your own green PRs):
 N1 E execution core (ADR-0014) → U4 X1–X2 (Notify / One-click / Auto PAPER for scans) → F portfolio risk.
 N2 P1-f/g/h (progression ladder + decision tools + context panel; manual-trading workspace polish, positions UI, adjustments, compare; education pages).
 N3 Remaining screener: U6 BK3–BK5, U8 EV3–EV4, U9 TX2–TX4, U7 LG2–LG4, U2 remaining blocks, U5 D3–D5, S5-C/S6–S10, U3 full gates.
 N4 Charting CH3–CH7 (price-action layers + replay, option-contract and spread charts, multi-chart workspace, study parity, TradingView adapter when access exists).
 N5 Copilot H-C4+ (memory, retrieval, explain & coach, UX streaming, governance, MCP); BYOK-V3.
 N6 G Trade engine port (only the modules the owner marks stable in the Trade repo; until then, the OI/levels pieces already ported).
 N7 Backtest C2–C6, B6–B7, D7 rest, I options analytics.
 N8 Phase 1 items (approval-only API orders behind flags, Capacitor/Tauri wrappers, BV vendor tier) — built and tested behind flags off; enabling them stays the owner's decision.
 N9 v1.3 crypto adapters, v1.2 IBKR build, K community/marketplace.
 Rule while N-work runs: any Phase 0 bug, beta feedback or security issue pre-empts N-work.

3b. Parked only while Z1–Z11 are open (do not start these before Z11 merges):
 E execution core and any API order placement; U4 auto-execution modes 3–4 (keep only Notify/One-click PAPER inside Z3); F portfolio risk; G Trade engine port; I options analytics beyond what P1/OI already ship; CH3–CH7 (except what U8 evidence needs); U2 remaining blocks beyond those used by shipped screens; U5 D3–D5 polish beyond Phase 0 pages; U7 beyond LG1; Copilot H-C4+ (memory, retrieval, MCP, multi-view); backtest C2–C6, B6–B7; D7 rest; BV vendor tier; Capacitor/Tauri store apps; crypto; IBKR; community/marketplace (K).
 Bug fixes and security fixes in any area are always in scope.

4. Reporting: keep docs/ROADMAP_STATUS.md with a "Phase 0" section at the top (Z1–Z11 status, % and blockers); every progress message starts with the Phase 0 line. Owner-side blockers to list every time: Hostinger server IP, R2 bucket/token in .env, Dhan partner application, company registration (for partner emails).
5. Exit criteria for Phase 0 (all must hold before inviting the first 50 users): Z1–Z11 merged; the acceptance flow in §1 passes end-to-end on staging with two test users on Upstox and Dhan; no API order path reachable; stale data ⇒ alerts suppressed; load test passed; rollback rehearsed.
