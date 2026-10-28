# OWNER ORDER - ATP COMPLETE AUDIT (28 Oct 2026) - verbatim

Roadmap: rows AUD-V1..V7, AUD-G1.1..G1.7, AUD-G2.1..G2.5, AUD-T1..T9 (docs/ROADMAP.md). Verification: docs/design/AUDIT_28OCT.md.

```text
OWNER ORDER — ATP COMPLETE AUDIT: VERIFY → GRADE → DIFFERENTIATE (28 Oct 2026). File as docs/owner-orders/ATP_ORDER_AUDIT_28OCT.md
verbatim; INBOX / DECISIONS / roadmap rows / README. Four overseer audits back this (repo b86710c; backend 405 tests run locally, 0
failed; 30+ web sources); every item carries file:line or a URL in docs/design/AUDIT_28OCT.md — write it first. No duplication: SCR-FIX,
DEF-0, UX-11b, UX-14, UI-STD, OI-V2, PX-1, BR are separate orders — reference, don't redo. NOT FILED YET in the repo (grep 0): AP-FIX,
CP-FIX, OI-V2, UI-STD, PX-1 — file them now from my 28 Oct messages before anything else. Rules: re-verify (first report = confirmed /
not reproduced per item), one PR per group, self-review every diff, tests, before / after proof page per PR, deploy line + migration
yes / no, reports ≤ 15 lines. ADR-0004 / ADR-0006 / TT-12 / signals-not-advice / English UI / public repo rules unchanged.

PRIORITY: V (verify-fixes) → G1 (compliance + reliability) → T1–T3 (trader tools, ranked by evidence) → G2 → T4+. Max 5 in progress.

══ V — FIX WHAT THE VERIFICATION FOUND WRONG (P1) ══
V1 Autopilot decides on the still-forming bar: workers/trading_worker.py:1685 → market_data/service.py:407-413 get_frames includes
   the open bar; no closed_only on this path (screener has it, runtime.py:115). Trim every bar with start + TF > now before analyze;
   parity test backtest == worker on the same closed bars. (= AP-FIX AP1; file AP-FIX as its own order + roadmap row.)
V2 Open P&L "–" after hours: portfolio/routes.py:43-68 prices only by a live adapter.get_ltp_for_symbol call; stored
   TradeRecord.mark_price (position_monitor.py:1343-1346, written only while a market family is open) is never used. Fallback per symbol
   to mark_price labelled "last mark HH:MM", count as priced; frontend dashboard/model.ts:177-188 fullyPriced accepts "mark" source.
   (= PX-1 P1.)
V3 VIX bands: frontend/src/dashboard/model.ts:142-147 vixNote hand-codes "Very calm / Calm / Elevated / Nervous" → call ui/levels.ts
   vixLevel + LevelChip (one shared table; backend ai/market_memory.py:228).
V4 Discipline score wording: journal/score.py counts open untagged trades as clean → "100 of 100" with 0 closed trades. Owner default:
   show "n/a until a trade closes" when closed = 0; otherwise append "(N open · M closed)".
V5 OI regime hysteresis (oi_regime.py:206-213): the BULLISH→BEARISH flip needs puts to FALL ≥ 2 %; with calls rising and puts flat a
   stale "BULLISH (Weakening)" can persist all day (the owner's 28 Oct Trade screenshot). Add the structural alternative: three
   consecutive negative Δ diff with call OI rising ≥ 2 % alone flips direction; document in OI_BANNER.md; test with the owner's
   numbers (Call 488.61 L, Put 369.53 L, Δ −57.38 L). Report whether Trade needs the same fix.
V6 Text-budget guard gap: e2e/textBudget.ts:10-37 reads only <p> / <li>; include leaf div / span text ≥ 15 words inside cards.
V7 Repo hygiene: ~560 live claude/* branches, 0 git tags, no CHANGELOG → delete merged branches (script + CI rule: delete on merge),
   semver tags from this release, CHANGELOG.md + in-app "What's new" sheet (one line per release).

══ G — INTERNATIONAL-GRADE WEB APP (facts from the audit; cite sources in AUDIT_28OCT.md) ══
G1 (P1, before public launch)
G1.1 DPDP rights: no self-service account erase / data export (erase_user only via team route team/routes.py:256; exports limited to
     orders / trades). Add /api/me/export (JSON + CSV bundle: profile, settings, trades, journal, AI history) and /api/me/erase
     (grace period, confirmation, audit), Settings → Account UI.
G1.2 Legal placeholders: legal/content/privacy.md:14,89 "[COMPANY NAME]", "[GRIEVANCE RESPONSE TIME]" → owner supplies fiduciary name
     + grievance officer; 90-day SLA per DPDP Rules; add docs/runbooks/BREACH.md (72-hour Data Protection Board notice).
G1.3 Prod exposure: FastAPI(main.py:213) without docs_url=None; Caddy guards only /metrics → disable /docs, /openapi.json, /redoc in
     production or gate behind SUPER_ADMIN; verify at runtime (curl in the deploy health step).
G1.4 Frontend error tracking: no browser Sentry → add @sentry/react with PII scrubbing; backend JsonFormatter gets an email / phone
     redaction filter (core/logging_config.py:45 redacts Telegram only).
G1.5 Core Web Vitals: no Lighthouse / web-vitals → Lighthouse CI job with budgets (LCP ≤ 2.5 s, INP ≤ 200 ms, CLS ≤ 0.1) on Today,
     Charts, Option chain, Terminal at phone + desktop; web-vitals RUM to /metrics.
G1.6 Dependabot / Renovate (none); ESLint + typescript-eslint (none; tsconfig strict only); ruff rule set widened (I, UP, ASYNC); mypy
     to all of app (today gated packages).
G1.7 SEBI retail-algo readiness (circular 4 Feb 2025, full force 1 Apr 2026): ATP = algo provider empanelled via the user's broker;
     static-IP + algo-ID tagging already modelled (compliance/static_ip.py, algo_id.py) → one "Compliance" page for the owner listing
     per-broker empanelment status, static IP, algo IDs, and the audit-trail export; "signals not advice" + RA-licence boundary text
     on the Risk page (lawyer to confirm).
G2 (P2)
G2.1 OpenTelemetry tracing (FastAPI + SQLAlchemy + httpx broker calls), trace id in JSON logs; N+1 profile of the top 5 list
     endpoints (positions, orders, chain, screener runs, journal) with SQL echo; eager loads where found.
G2.2 Live prices end to end: broker WS (market_data/stream.py) → SSE push; remove the four 60 s setInterval pollers; stale chip
     driven by tick age.
G2.3 Self-host fonts, CSP without style-src 'unsafe-inline', preload critical assets; manifest maskable icon + screenshots; iOS
     Capacitor target (later).
G2.4 Activation / time-to-first-value metrics: atp_activation_total{step} (signup → broker connect → first chart → first paper trade
     → first journal review) + admin funnel; target: first paper trade within the first session.
G2.5 Outbound signed webhooks (user-defined) and WhatsApp Business channel (owner task: number) — after Telegram in-place connect.

══ T — TRADER TOOLS NOBODY OFFERS (evidence: SEBI study 93 % of F&O traders lose, avg ₹2 L; user complaints: app outages in market
     hours, paywalls, margin penalties on expiry, SL slippage, "why" missing, info overload; no Indian tool found offering the items
     below — sources in AUDIT_28OCT.md). All information-only, user-approved actions, behind flags, PAPER first. Each = its own roadmap
     row; build on existing pieces (positions, journal, rhythm, reader, Telegram).
T1 "My day in one card" (Today, top): for each open position — distance of the short strike to the nearest area, expiry in N
   sessions, what changed since yesterday (gap at open, zone tested / swept / broken, event today, global cue), and one state chip
   (Safe / Watch / Near invalidation). Position-aware pre-market brief — no competitor found (Google Finance briefs are generic;
   TradeZella's sentiment agent is not position-based).
T2 Expiry-day & margin checklist (positional sellers' #1 margin pain, TradingQnA): T-1 session: positions expiring tomorrow, ITM
   risk, physical-settlement / expiry-margin reminder per broker's rule (text from the broker's own help page, linked), roll
   suggestion in plain words (next weekly), one tap "add to basket" — never auto.
T3 "Why no trade today" timeline (BR-S1 log, surfaced early): last N closed bars with the reason (no signal / area not reached / risk
   setting / stale data / outside hours / contract missing) — the most common beginner confusion; no competitor found.
T4 Paper report card (forward-test, not backtest): per strategy / bot — closed trades, win %, avg R, capture % of credit, drawdown,
   plan adherence (from journal tags), calibration of any shown confidence (Brier) once ≥ 20 trades; shareable image. Stockmock /
   AlgoTest do backtests only.
T5 Structure reading as a service: the Trade reader (P5-1 port) exposed as "Read this chart" on any symbol / TF with the engine's
   areas, lifecycle, pullback state and commitment candle marked, answer-key score shown ("verified on N hand-marked cases") — no
   competitor found; signals-not-advice wording; public / education surfaces keep the 3-month-old-chart rule.
T6 Similar-setup recall (BR-M3) + post-trade "what the context said vs what happened" (BR-R1): surfaced in the journal row and on
   the approval card; TradeZella has setup playbooks but no structure-based recall and nothing Indian.
T7 Cost-of-trading meter (SEBI: ~₹26,000 / trader / year): running YTD charges vs gross P&L on My trades + per-trade charge share;
   contract-note import already exists — show the number traders never see.
T8 Reliability promise the market lacks (complaints: Sensibull / Quantsapp outages in market hours): public status page (uptime per
   market session, data freshness), in-app banner on degradation, and the SLOs (docs/SLO.md) published — a differentiator by itself.
T9 Pricing (owner decision, defaults): free tier = charts + chain + paper + journal; Pro ₹499–₹999 / month in line with Opstra ₹1,300,
   Quantsapp ₹1,350–2,950, Stockmock ₹349–2,999, Sensibull free-for-broker-clients; AI included in every paid plan with a daily cap
   (TradeZella model), no AI upsell; 14-day trial exists (plans/registry.py). Lawyer check on RA boundary before charging.

PROOF: V = tests + before / after screenshots (positions after hours, Autopilot replay parity); G1 = checklist page in admin with
each item's status; T = one comparison page per tool on phone + desktop, dark + light, with the source complaint it answers quoted.
```
