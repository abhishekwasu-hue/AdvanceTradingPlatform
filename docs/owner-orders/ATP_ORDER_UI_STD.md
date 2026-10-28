# Owner order UI-STD - UI standards audit fix pack (28 Oct 2026, verbatim)

**Superseded by UI-28 (docs/owner-orders/ATP_ORDER_UI_28OCT.md, 28 Oct); kept as filed.**

Roadmap: P1 row UI-STD (groups A and B), Phase 2 row UI-STD-C (group C). Extends UX-11 / UX-11b / UX-14 / SCR-FIX.
Sources per item: docs/design/UI_STANDARDS_AUDIT.md.

```
OWNER ORDER — UI STANDARDS AUDIT FIX PACK (28 Oct). Roadmap row "UI-STD" (P1 for A/B, P2 for C). Extends UX-11 / UX-11b / UX-14 /
SCR-FIX (do not redo what they already cover; reference them). File in INBOX verbatim / DECISIONS / roadmap / README first. Two
overseer read-only audits (repo 148577c) compared the v2 UI with (1) mainstream decision-support tools (Sensibull, TradingView,
Streak, Chartink, Edgewonk; Kite / Upstox only for ticket / positions / orders) and (2) published UX standards (NN/g heuristics, WCAG
2.2 AA, Material, Apple HIG). Every item below has a source in docs/design/UI_STANDARDS_AUDIT.md — write that file first from this
order (table + sources) so nothing is unsourced. Rule: ATP never shows anything a mainstream tool does not show unless it is a
measured improvement (section C); no theory on screen; nothing that confuses a trader. Re-verify each item (first report: confirmed /
not reproduced), then one PR per group, self-review every diff, tests, before / after phone 390 px (dark + light) + desktop in ONE
comparison page per PR.

A — REMOVE THE EXTRAS (what no mainstream tool shows)
A1 "Practice mode (PAPER) · No real order is sent and no money moves." frame on every ticket / positions (fno/OrderTicketV2.tsx:258-266,
   PositionsV2.tsx:226) → one "PAPER" chip in the header (Streak labels the mode once). Button text "Buy" / "Sell" (not "Place PAPER
   order"); the disabled state shows ONLY the blocking line (ticketModel.ts:177-182) and never "Cannot place" beside "Information only"
   (pairs with DEF-0 D6).
A2 Chain "What the chain shows" prose block + note (optionChain/ChainDishV2.tsx:55-66) → removed; numbers stay in SummaryStripV2.
   Payoff sentence (fno/BasketPanelV2.tsx:475) and the ticket's plain-words payoff (OrderTicketV2.tsx:~476) → ⓘ Details.
A3 Signals paragraphs (signals/SignalPartsV2.tsx:240,356,392,435,455) → one PAPER chip + ⓘ; Streak-style card = Symbol · Action ·
   Price · Approve / Skip / Chart, "Why" stays in <details>.
A4 ScreenQL never visible by default: StageRow.tsx:148, LibraryTools.tsx:181-184, alert condition (fno/ChainActionSheetV2.tsx:99-105 →
   "Alert when NIFTY 24500 CE ≥ 120"), AlertRulesPanel.tsx:112, LegendCard.tsx:101 — plain words; code under "Advanced" (SCR-FIX D7).
A5 Journal: raw JSON.stringify(evidence) on screen (pages/JournalPage.tsx:105) → never; scoring explanations (:48-49, 243, 293) →
   "How scored" details.
A6 Watchlist intro / "lists fill after the daily update" sentences (watchlist/model.ts:19-23) → empty-row state only.
A7 Jargon out of user strings: "Phase 0 has PAPER only." (trading/OrdersV2.tsx:84), "engine" (screener/EvidencePanel.tsx:306,
   pages/StrategyBuilderPage.tsx:229), "dish" (signals/DayOneV2.tsx:25, data-block ids), Beginner / Intermediate labels
   (progression/samples.experience.ts:6-9 → lock icon only, gate server-side, per DEF-0 D4). Add a user-string lint test (pattern of
   englishOnlyOutsideInterview.test.tsx) banning: Phase 0, PAPER only, ScreenQL, engine, dish, tenant, flag, information only.
A8 Disclaimers: 51 free-text "information only / not advice / does not place" literals → ONE <Disclaimer> per page (ui/Disclaimer.tsx).

B — STANDARDS FLOOR (measured failures)
B1 Errors (NN/g H9): 175 setError(String(e)) + ErrorBoundary showing error.name (components/ErrorBoundary.tsx:8) + backend
   detail=str(exc) 146× with ids ("alert rule #…" screener/routes.py:416, "Account #…" deployments/routes.py:492) → friendlyError inside
   ErrorState; backend maps exceptions to a user sentence + machine code, logs the id server-side; 30 raw {error} spots → ErrorState
   with Retry.
B2 Disabled buttons (NN/g H6): 271 of 284 give no reason → Button prop `reason` rendered as a helper line, or keep enabled and explain
   on tap.
B3 One component per job (NN/g H4): 120 raw <select> → Select primitive (UX-5); 28 window.confirm → ConfirmDialog; 81 ad-hoc P&L
   colour ternaries + 261 toFixed/toLocaleString → Signed + format.ts; 37 raw <details> → Details. Extend ui/signedLint.test.ts to
   ban raw <select>, window.confirm, toFixed in V2 files.
B4 Financial confirm (NN/g H5): Exit-all / exit on every account (OrderTicketV2.tsx:432) without confirm → ConfirmDialog with the ₹
   impact; replace native confirms (terminal/PanelsV2.tsx:334).
B5 Borders (WCAG 1.4.11): with colour_v2 OFF border/card = 1.32:1 → v2 / v3 border tokens become the default (UX-14 palette); input
   and chip outlines ≥ 3:1. Muted text: adopt the v3 --fg-muted so labels sit visibly below numbers.
B6 Bottom nav (Material / HIG 3–5): shell/navModel.ts:207-213 gives 6 with the watchlists flag → owner default: remove Watchlist from
   the bar (it lives in the Charts panel); 5 incl. More; no whitespace-nowrap tracking-tight hacks (BottomNavV2.tsx:55).
B7 Targets (HIG 44 pt): Button size="sm" (32 px) 88× in tap-heavy pages → v2 size default on phone; 8 px gap between adjacent chips
   (OiAlertsCard); links get the same 44 px hit area.
B8 Reflow (WCAG 1.4.10): grid-cols-4/5/6 without breakpoint 154× (RiskPartsV2 ×2, MarketsPageV2 ×2, SignalsV2) → 2 cols on phone;
   optionsBuilder/LegTable.tsx:146 min-w 880 px → card layout < md; LibraryTools.tsx:113 min-w-32rem → wrap.
B9 Loading: 45 "Loading…" texts + 36 ad-hoc animate-pulse → Skeleton / SkeletonRows (Feedback.tsx:38).
B10 Inputs: 37 raw <input> without label in V2 → Input primitive. SVG axis text fontSize 10 (EvidenceChart.tsx:62-81,
    CandleSnapshotV2.tsx:44-62) → 11–12 px with --chart-text.
B11 Tables: numeric columns right-aligned + font-tabular by default in Table / KeyValueTable (today 48 of 410 <td> right-aligned).
B12 Freshness: one Freshness primitive ("as of HH:MM" / "delayed") on every number card (Today, Signals cards missing it).
B13 SEBI risk disclosure: the four SEBI statements verbatim on the Risk disclosure page with a one-time "I understand" (owner default;
    obligation on ATP itself unverified — legal review later).

C — BEYOND THE BEST (each derived from a cited user complaint about the best-in-class tool; UI only; behind flags)
C1 Option chain: "Lots / Qty" unit toggle in the column menu + an "ATM ±N" strike-range chip row above the table (Sensibull users:
   clutter on phone); spot header shows signed Δ and % (Kite); IV percentile card under More.
C2 Builder: payoff panel row "Exit when basket P&L ≤ −₹X / ≥ +₹Y" that writes the stop into the ticket before placing (uses the
   existing CX engine; Sensibull users ask for conditional basket exits).
C3 Alerts: an Alerts tab listing every alert with its symbol chip and an "orphaned" badge when the symbol leaves the list; no cap
   (TradingView complaint).
C4 Screener: results board with Auto-refresh switch, column picker, sortable columns, CSV export, "Run on this watchlist" chip
   (TradingView / Chartink).
C5 Deploy card: the exact F&O contract it will trade ("NIFTY 24500 PE · 03 Nov") beside the status chip (Streak users).
C6 Journal: expectancy + avg R tiles, drawdown line, "Export all (CSV)" in the header (Edgewonk users).
C7 Order ticket: "Required ₹ / Available ₹" pair that recalculates on every lot tap (Kite users).
C8 Daily brief tiles (VIX, FII / DII, sectors) per UX-11b — no further source found; keep to UX-11b.
Order: PR 1 = A1–A8 (+ lint), PR 2 = B1–B4, PR 3 = B5–B13, PR 4+ = C1–C7 one each. Report ≤ 15 lines per PR; deploy line + migration
yes / no.
```
