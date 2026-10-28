# UI standards audit - sources per item (UI-28 consolidated UI order, 28 Oct)

Order: docs/owner-orders/ATP_ORDER_UI_28OCT.md (replaces ATP_ORDER_UI_STD.md; its A / B / C tables below map to U1-U7). Findings come from two overseer read-only audits of repo commit 148577c.
Status column: filled in the first report ("confirmed" / "not reproduced" after a grep on today's main), then per PR.
Rule: ATP shows nothing a mainstream tool does not show unless it is a measured improvement (group C); no theory on screen.

## Sources

| Key | Source |
|---|---|
| NNG | Nielsen Norman Group, "10 Usability Heuristics for User Interface Design" (H1-H10) - https://www.nngroup.com/articles/ten-usability-heuristics/ |
| WCAG | W3C, Web Content Accessibility Guidelines 2.2, level AA - https://www.w3.org/TR/WCAG22/ |
| MAT | Material Design 3, Navigation bar: 3-5 destinations - https://m3.material.io/components/navigation-bar/guidelines |
| HIG | Apple Human Interface Guidelines: tab bars (3-5 tabs) and Accessibility (hit targets at least 44 x 44 pt) - https://developer.apple.com/design/human-interface-guidelines/ |
| SNS | Sensibull (option chain, strategy builder, positions) - https://web.sensibull.com |
| TV | TradingView (screener, alerts, charts) - https://www.tradingview.com |
| STK | Streak (strategy deploy cards, paper / live mode label) - https://www.streak.tech |
| CHK | Chartink (screener results board) - https://chartink.com |
| EDG | Edgewonk (trading journal statistics) - https://edgewonk.com |
| KITE | Zerodha Kite (order ticket, positions, orders; required / available margin) - https://kite.zerodha.com |
| UPX | Upstox (order ticket, positions, orders) - https://upstox.com |
| SEBI | SEBI risk disclosure for individual traders in equity F&O (four statements); exact circular link to be confirmed at B13 |
| OVR | Overseer audit, repo 148577c (file:line and counts below); counts are re-measured in the first report |

## A - remove the extras (what no mainstream tool shows)

| ID | Finding (OVR) | Mainstream / standard | Fix | Status |
|---|---|---|---|---|
| A1 | "Practice mode (PAPER) · No real order is sent…" frame on every ticket / positions (fno/OrderTicketV2.tsx:258-266, PositionsV2.tsx:226); button "Place PAPER order"; "Cannot place" beside "Information only" (ticketModel.ts:177-182) | STK (mode labelled once), KITE / UPX ("Buy" / "Sell"), NNG H8 (minimalist), NNG H9 | one "PAPER" chip in the header; "Buy" / "Sell"; disabled state shows only the blocking line (with DEF-0 D6) | |
| A2 | Chain "What the chain shows" prose (optionChain/ChainDishV2.tsx:55-66); payoff sentence (fno/BasketPanelV2.tsx:475); ticket plain-words payoff (OrderTicketV2.tsx:~476) | SNS (numbers, no prose), NNG H8 | prose removed; numbers stay in SummaryStripV2; payoff text in ⓘ Details | |
| A3 | Signals paragraphs (signals/SignalPartsV2.tsx:240, 356, 392, 435, 455) | STK (card: Symbol · Action · Price · actions), NNG H8 | one PAPER chip + ⓘ; card = Symbol · Action · Price · Approve / Skip / Chart; "Why" in details | |
| A4 | ScreenQL visible by default (StageRow.tsx:148, LibraryTools.tsx:181-184, fno/ChainActionSheetV2.tsx:99-105, AlertRulesPanel.tsx:112, LegendCard.tsx:101) | TV / CHK (plain conditions), NNG H2 (user's language) | plain words ("Alert when NIFTY 24500 CE ≥ 120"); code under "Advanced" (SCR-FIX D7) | |
| A5 | Journal raw JSON.stringify(evidence) (pages/JournalPage.tsx:105); scoring explanations (:48-49, 243, 293) | EDG (numbers, no raw data), NNG H2 / H8 | never raw JSON; explanations in "How scored" details | |
| A6 | Watchlist intro and "lists fill after the daily update" sentences (watchlist/model.ts:19-23) | KITE / TV watchlists (no intro text), NNG H8 | empty-row state only | |
| A7 | Jargon: "Phase 0 has PAPER only." (trading/OrdersV2.tsx:84), "engine" (screener/EvidencePanel.tsx:306, pages/StrategyBuilderPage.tsx:229), "dish" (signals/DayOneV2.tsx:25), Beginner / Intermediate labels (progression/samples.experience.ts:6-9) | NNG H2, CLAUDE.md §2 (no developer text), DEF-0 D4 | words out; lock icon only (gate server-side); user-string lint test banning Phase 0, PAPER only, ScreenQL, engine, dish, tenant, flag, information only | |
| A8 | 51 free-text "information only / not advice / does not place" literals | NNG H4 (consistency), H8 | one `<Disclaimer>` per page (ui/Disclaimer.tsx) | |

## B - standards floor (measured failures)

| ID | Finding (OVR) | Standard | Fix | Status |
|---|---|---|---|---|
| B1 | 175 setError(String(e)); ErrorBoundary shows error.name (components/ErrorBoundary.tsx:8); backend detail=str(exc) 146× with ids (screener/routes.py:416, deployments/routes.py:492); 30 raw {error} | NNG H9 (plain-language errors, a way out) | friendlyError in ErrorState with Retry; backend: user sentence + machine code, id logged server-side | |
| B2 | 271 of 284 disabled buttons give no reason | NNG H1 / H6 | Button `reason` helper line, or enabled and explained on tap | |
| B3 | 120 raw `<select>`, 28 window.confirm, 81 ad-hoc P&L colour ternaries, 261 toFixed / toLocaleString, 37 raw `<details>` | NNG H4 | Select (UX-5), ConfirmDialog, Signed + format.ts, Details; ui/signedLint.test.ts bans raw select / window.confirm / toFixed in V2 files | |
| B4 | Exit-all / exit on every account without confirm (OrderTicketV2.tsx:432); native confirm (terminal/PanelsV2.tsx:334) | NNG H5 (error prevention for costly actions) | ConfirmDialog with the ₹ impact; exits stay unblocked (ADR-0004) | |
| B5 | colour_v2 OFF: border on card 1.32:1; muted text close to numbers | WCAG 1.4.11 (non-text contrast ≥ 3:1), 1.4.3 | v2 / v3 border tokens the default (UX-14); input and chip outlines ≥ 3:1; v3 --fg-muted | |
| B6 | 6 bottom-bar items with the watchlists flag (shell/navModel.ts:207-213); nowrap / tracking hacks (BottomNavV2.tsx:55) | MAT (3-5), HIG (tab bars) | Watchlist off the bar (in the Charts panel); 5 incl. More | |
| B7 | Button size="sm" (32 px) 88× on tap-heavy pages; chips without gap (OiAlertsCard) | HIG (44 pt), WCAG 2.5.8 (target size) | v2 size on phone; 8 px chip gap; 44 px link hit area | |
| B8 | grid-cols-4/5/6 without breakpoint 154× (RiskPartsV2, MarketsPageV2, SignalsV2); LegTable.tsx:146 min-w 880 px; LibraryTools.tsx:113 min-w-32rem | WCAG 1.4.10 (reflow at 320 CSS px) | 2 columns on phone; cards below md; wrap | |
| B9 | 45 "Loading…" texts, 36 ad-hoc animate-pulse | NNG H1 / H4 | Skeleton / SkeletonRows (Feedback.tsx:38) | |
| B10 | 37 raw `<input>` without label in V2; SVG axis text 10 px (EvidenceChart.tsx:62-81, CandleSnapshotV2.tsx:44-62) | WCAG 1.3.1 / 3.3.2 (labels), 1.4.4 | Input primitive; 11-12 px with --chart-text | |
| B11 | 48 of 410 `<td>` right-aligned | NNG H4; KITE / SNS tables (numbers right, tabular) | Table / KeyValueTable: numeric columns right + font-tabular by default | |
| B12 | Number cards without an "as of" (Today, Signals) | NNG H1 (system status); KITE / TV (time stamps) | one Freshness primitive on every number card | |
| B13 | No SEBI risk statements | SEBI (obligation on ATP itself unverified; legal review later) | four statements verbatim on Risk disclosure, one-time "I understand" | |

## C - beyond the best (UI only, behind flags; each from a user complaint about the best tool)

| ID | Complaint (as given in OVR; public link attached at the item's first report) | Fix |
|---|---|---|
| C1 | SNS users: chain clutter on phone; KITE shows signed spot change | Lots / Qty toggle; "ATM ±N" chip row; spot Δ and %; IV percentile under More |
| C2 | SNS users ask for conditional basket exits | payoff row "Exit when basket P&L ≤ −₹X / ≥ +₹Y" writing the stop into the ticket (CX engine) |
| C3 | TV: alert cap, alerts lost when a symbol leaves a list | Alerts tab, symbol chip, "orphaned" badge, no cap |
| C4 | TV / CHK screener results | results board: auto-refresh, column picker, sort, CSV, "Run on this watchlist" |
| C5 | STK users: which contract a deploy will trade | the exact contract ("NIFTY 24500 PE · 03 Nov") beside the status chip |
| C6 | EDG users: expectancy, R and drawdown | expectancy + avg R tiles, drawdown line, "Export all (CSV)" |
| C7 | KITE users: margin before placing | "Required ₹ / Available ₹" recalculated on every lot tap |
| C8 | no further source found | keep to UX-11b |

Overlaps (reference, do not redo): UX-11 / UX-11b (numbers first, Signed, chips), UX-14 (palette, colour_v3 ON), UX-15 (Positions:
the "PAPER" frame sentence under Positions already a chip), SCR-FIX D7 (ScreenQL under Advanced), DEF-0 D4 / D6.

## UI-28 map (consolidated order) and the items it adds

| UI-28 | From | Extra source for new parts |
|---|---|---|
| U1.1-U1.6 | A1-A6 | as above |
| U1.7 | A7 + "Not available in Phase 0" -> "LIVE: not yet enabled"; Experience card hidden unless the gate is on | NNG H2; DEF-0 D4 |
| U1.8 | A8 + B13 | SEBI; NNG H8 |
| U1.9 | new: "Sessions 1 of 20" on both the Experience card and the Progression ladder | NNG H4 / H8 (one place) |
| U2.1-U2.5 | UX-11 / UX-11b (ATP_ORDER_UX11.md, ATP_ORDER_UX11B.md) | WCAG 1.4.1 (colour not alone), 1.4.3; TV / KITE (Signed ▲ ▼, tabular) |
| U3.1-U3.4 | B1-B4 | as above; U3.4 press-and-hold for LIVE: HIG (confirm destructive / costly actions) |
| U3.5-U3.9 | B6-B11, B9, B12 + Screener reflow (SCR-FIX UI parts) | WCAG 1.4.10 (reflow), MAT, HIG |
| U4.1-U4.4 | UX-14 (ATP_ORDER_UX14.md) + B5 | WCAG 1.4.3 / 1.4.11; TV / KITE dark palettes |
| U5.1-U5.3 | SCR-FIX UI parts (Show on chart, Telegram in place, results board) + C4 | TV / CHK (results board, chart from a match); NNG H3 / H9 (no dead ends) |
| U6.1-U6.4 | OI view order (components exist, mounted only on OptionChainPage.tsx:116) | SNS (OI views and OI change tables) |
| U7.1-U7.7 | C1-C3, C5-C7 + deploy preview, PAPER margin estimate, Copilot cost chip / folded tabs | as in C; KITE (margin calculator) |
