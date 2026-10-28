# Owner order UI-28 - Consolidated UI order (28 Oct 2026, verbatim)

Roadmap: P1 row UI-28 (U1-U7, one PR each). Replaces the separate UI orders of 27-28 Oct (UX-11, UX-11b, UX-14, UI-STD,
Screener UI, Telegram in place, OI view, PX-1 P5 / P6); those files stay (never deleted), marked superseded. Sources per item:
docs/design/UI_STANDARDS_AUDIT.md.

```
Avoid common prompts,

OWNER — CONSOLIDATED UI ORDER (28 Oct 2026). Replaces every separate UI message I sent on 27–28 Oct (UX-11, UX-11b, UX-14, UI-STD,
Screener UI, Telegram in place, OI view, PX-1 P5/P6). File as docs/owner-orders/ATP_ORDER_UI_28OCT.md verbatim, then INBOX /
DECISIONS / roadmap / README. Scope: USER INTERFACE ONLY — layout, screens, text, colour, components. Not logic, not data paths
(those orders stay separate). Owner's vision: an app better than the market's best. Rules: nothing that confuses a trader; no theory on
screen (pro traders find it boring); numbers first, text on demand; every item below is backed by a read-only overseer audit with
file:line and a public source (write docs/design/UI_STANDARDS_AUDIT.md first: table + sources, so nothing is unsourced). Re-verify
each item (first report = confirmed / not reproduced); one PR per group; self-review every diff; tests; behind ui_v2; before / after
phone 390 px (dark + light) + desktop in ONE comparison page per PR; reports ≤ 15 lines; migration: no (UI only).

ORDER OF PRs: U1 → U2 → U3 → U4 → U5 → U6 → U7 (one each). Max 3 in progress. Trader-function bugs keep priority if they conflict.

U1 — REMOVE WHAT NO MAINSTREAM TOOL SHOWS (benchmarks: Sensibull, TradingView, Streak, Chartink, Edgewonk; Kite / Upstox for ticket)
U1.1 "Practice mode (PAPER) · No real order is sent…" frame on every ticket / positions (fno/OrderTicketV2.tsx:258-266,
     PositionsV2.tsx:226) → one "PAPER" chip in the header. Buttons "Buy" / "Sell" (not "Place PAPER order"). A disabled button shows
     ONLY the blocking line (ticketModel.ts:177-182); never "Cannot place" beside "Information only".
U1.2 Chain "What the chain shows" prose block + note (optionChain/ChainDishV2.tsx:55-66) → removed; numbers stay in SummaryStripV2.
     Payoff sentence (fno/BasketPanelV2.tsx:475) and the ticket's plain-words payoff (OrderTicketV2.tsx:~476) → ⓘ Details.
U1.3 Signals paragraphs (signals/SignalPartsV2.tsx:240,356,392,435,455) → PAPER chip + ⓘ; card = Symbol · Action · Price · Approve /
     Skip / Chart; "Why" stays in <details>.
U1.4 ScreenQL never visible by default: StageRow.tsx:148, LibraryTools.tsx:181-184, AlertRulesPanel.tsx:112, LegendCard.tsx:101;
     alert text in plain words ("Alert when NIFTY 24500 CE ≥ 120", fno/ChainActionSheetV2.tsx:99-105); code under "Advanced".
U1.5 Journal: raw JSON.stringify(evidence) on screen (pages/JournalPage.tsx:105) → never; scoring explanations (:48-49,243,293) →
     "How scored" details.
U1.6 Watchlist intro / "lists fill after the daily update" (watchlist/model.ts:19-23) → empty-row state only.
U1.7 Jargon out of user strings: "Phase 0 has PAPER only." (trading/OrdersV2.tsx:84), "Not available in Phase 0" (progression ladder)
     → "LIVE: not yet enabled"; "engine" (screener/EvidencePanel.tsx:306, pages/StrategyBuilderPage.tsx:229); "dish"
     (signals/DayOneV2.tsx:25, data-block ids); Beginner / Intermediate labels and the Experience-level card (progression/
     samples.experience.ts:6-9) → hidden unless the user enabled the gate; lock icon only. Lint test on user strings (pattern of
     englishOnlyOutsideInterview.test.tsx) banning: Phase 0, PAPER only, ScreenQL, engine, dish, tenant, flag, information only.
U1.8 Disclaimers: 51 free-text "information only / not advice / does not place" literals → ONE <Disclaimer> per page footer
     (ui/Disclaimer.tsx); the SEBI signals-not-advice line once per page. Risk page: the four SEBI risk-disclosure statements verbatim
     with a one-time "I understand" (owner default).
U1.9 Experience card + Progression ladder both show "Sessions 1 of 20" → one place.

U2 — NUMBERS FIRST, LOGICAL COLOUR (UX-11 + UX-11b)
U2.1 Colour rules, one table used everywhere, always with ▲ ▼ ◆ or an icon, never colour alone, WCAG AA dark + light, CVD mode kept:
     green --up = up / profit / pass / inside limits; red --down = down / loss / fail / breached; amber --warn = caution / elevated /
     stale / near invalidation / CHoCH / holiday / ≥ 80 % of a budget; blue --info = information / link / action; grey = neutral /
     none / sideways. Colour encodes meaning only. CI contrast test for every chip tone on every surface token.
U2.2 Card = title + numbers / table / chart + at most ONE short line (≤ 15 words); everything else in the shared ⓘ Details (closed by
     default, state remembered). Shared primitives: Details, Signed, KeyValueTable, ProgressStat, PageFootnote, Chip, TrendChip (new:
     UP / DOWN / SIDEWAYS), LevelChip (new: VIX / mood / budget bands); merge the two Signed components (components/primitives/
     Feedback.tsx vs ui/Signed.tsx). Empty state = one line + one action.
U2.3 Today briefs (DailyRhythmCardV2.tsx:20-46, types.ts:5-14; backend sends typed rows per section, lines kept for Telegram):
     MORNING — Global cues: mood chip + table Market | Last | Chg % (Signed), amber "delayed" when stale; India VIX: value | Chg % |
     level chip (one shared band table: Low / Normal / Elevated / High) | 1-year percentile bar; FII / DII: two Signed ₹ cr + "as of";
     Events: chips, empty → one muted line + link; Structure: table Index | Daily | Weekly | Last event | Location with TrendChips,
     BOS chip by direction, CHoCH amber, location chip, amber "last read <date>" if stale; Setups: Symbol | side chip | source | ⓘ;
     Positions: Symbol | Side | Open P&L (Signed) | amber "near invalidation"; Risk budget: bar (amber ≥ 80 %, red 100 %) + "losses
     in a row 2 / 3" chip. PULSE: same Structure table + positions near invalidation + new setups + Day block (Booked / Open P&L
     Signed, limits chip Inside ✓ / Breached ✕, discipline number + bar). WRAP: Index | Close | Chg %; breadth green-red bar;
     strongest / weakest sector Signed rows; tomorrow chips. WEEK: 5-session table + Structure table; Closed / Won / Lost / Net
     (Signed); discipline ▲▼ vs last week; days-inside-limits bar; costliest pattern Signed; next week's events as chips. Footer:
     one PageFootnote. Owner default: support / resistance values shown as a price range inside ⓘ Details on the user's OWN brief only.
U2.4 Dish pages: Positions (dishes.py:224,232-236,252 → KeyValueTable + Signed + progress bars; drop "Phase 0 has PAPER only."
     PositionsV2.tsx:309); Signals (dishes.py:111 → table Strategy | Closed | Won | Lost | Win %; :123 waiting → chip; :140-144 → chip
     row; "Nothing is placed until you approve it" ×3 + DayOneV2.tsx:281 → once in footer); Journal (dishes.py:305-311 score →
     number + ▲▼ + sparkline; :316-319 habits → Tag | Trades | Net; :338-342 best / worst → 2-row table; expectancy + avg R tiles,
     drawdown line, "Export all (CSV)" in the header); Charts dish (dishes.py:389-458 → trend chips); Markets (markets/model.ts:179,
     186,191; MarketsPageV2.tsx:96-97 → Signed values, leader / laggard chips, sectors-up bar); Risk (RiskV2.tsx:163,220,285,298
     "exits never blocked" ×4 → once; :233,:357 → Details); Paper bot (PaperBotV2.tsx:246 botWhat() → key-value; :173,273,465 → one
     line + Details; show "Why no trade" and "Last signal" lines, today's + open P&L Signed, tap → legs; one-tap "Exit this bot's
     positions"); Option chain intro (OptionChainPageV2.tsx:189), Settings (TradingDefaultsV2.tsx:254), Options builder
     (OptionsBuilderPage.tsx:207) → one line + Details; Copilot answers: structured rows + Signed; locales/en.ts:203 dev text out.
U2.5 Guards: e2e/textBudget.ts:12 skips <li> → include list items; extend text-budget-v2 to Today (all four tabs), Markets, dish
     pages; "signed number rendered without Signed" lint.

U3 — STANDARDS FLOOR (NN/g, WCAG 2.2 AA, Material, Apple HIG; measured)
U3.1 Errors: 175 setError(String(e)) + ErrorBoundary showing error.name (components/ErrorBoundary.tsx:8) + 30 raw {error} spots →
     friendlyError inside ErrorState with Retry; never an exception name or "#id" on screen (backend returns a user sentence + code).
U3.2 Disabled buttons: 271 of 284 give no reason → Button prop `reason` rendered as a helper line, or keep enabled and explain on tap.
U3.3 One component per job: 120 raw <select> → Select primitive (UX-5); 28 window.confirm → ConfirmDialog; 81 ad-hoc P&L colour
     ternaries + 261 toFixed / toLocaleString → Signed + format.ts; 37 raw <details> → Details; 37 unlabelled <input> → Input.
     Extend ui/signedLint.test.ts to ban raw <select>, window.confirm, toFixed in V2 files.
U3.4 Financial confirm: Exit-all / exit on every account (OrderTicketV2.tsx:432) → ConfirmDialog with the ₹ impact; replace native
     confirms (terminal/PanelsV2.tsx:334). Press-and-hold for LIVE on phone (UX-9).
U3.5 Bottom nav: shell/navModel.ts:207-213 gives 6 with the watchlists flag → owner default: remove Watchlist from the bar (it lives in
     the Charts panel); 5 incl. More; no whitespace-nowrap tracking-tight (BottomNavV2.tsx:55).
U3.6 Targets: Button size="sm" (32 px) 88× in tap-heavy pages → v2 (44 px) default on phone; 8 px gap between adjacent chips
     (OiAlertsCard); links get the same 44 px hit area. Run / Save on the Screener bar: solid primary, 44 px, Save with a text label.
U3.7 Reflow 320–390 px: grid-cols-4/5/6 without breakpoint 154× (RiskPartsV2 ×2, MarketsPageV2 ×2, SignalsV2) → 2 cols on phone;
     optionsBuilder/LegTable.tsx:146 min-w 880 px → cards < md; LibraryTools.tsx:113 min-w-32rem → wrap; ScreenQL / condition text
     never overflows its box (ParamInline.tsx:130,134 size={text.length+1}; StageRow.tsx:145-147,272,58; AppShellV2.tsx:230 main
     overflow); Conditions footer flex-wrap (FunnelCanvas.tsx:124-130,148-161); rotated "All" label → horizontal chip
     (FunnelCanvas.tsx:87, StageRow.tsx:186); Results table stacked rows on phone, sticky symbol (ResultBoard.tsx:59-84); evidence
     verify table wraps (EvidencePanel.tsx:150-168,136); chart grid / heatmap one column on phone (ResultViews.tsx:72-79,34,42);
     "Link your Telegram…" stacks above the button (EvidencePanel.tsx:177-181). CI: Playwright 390 px dark + light, assert document
     scrollWidth ≤ clientWidth and no element overflows its card.
U3.8 Loading: 45 "Loading…" texts + 36 ad-hoc animate-pulse → Skeleton / SkeletonRows (Feedback.tsx:38). Freshness: one Freshness
     primitive ("as of HH:MM" / "delayed") on every number card (Today, Signals cards missing it).
U3.9 Tables: numeric columns right-aligned + font-tabular by default in Table / KeyValueTable (today 48 of 410 <td>). SVG axis text
     fontSize 10 → 11–12 px with --chart-text (EvidenceChart.tsx:62-81, CandleSnapshotV2.tsx:44-62).

U4 — PROFESSIONAL PALETTE (UX-14; new token set colour_v3 behind a flag; owner picks on ONE comparison page before it becomes default)
U4.1 Findings (tokens.css:461-480, colour_v2 dark): cards 32 43 70 saturated navy; brand-fill 30 64 175 on them = 1.61:1 (the
     invisible Run button); borders 128 144 182 bright; with colour_v2 OFF border/card = 1.32:1; too many accents (--ai, --ai-2,
     --signal, --armed, --accent-2, the purple dot backdrop UX-12/13).
U4.2 Palette (verify every ratio in CI; adjust by the smallest step): dark page #0B0E14 · card #131722 · raised #1C2130 · border
     #2A2F3D · text #E6E9EF · muted #9AA3B2 · brand #2962FF (white text) · up #2EBD85 · down #F6465D · warn #F0A93B · neutral #8B93A7 ·
     ai #8B5CF6 (AI badge only); light page #F7F8FA · card #FFFFFF · raised #F1F3F6 · border #E3E6EB · text #0F172A · muted #5B6475 ·
     brand #2962FF · up #0A7D55 · down #D92D3F · warn #B45309 · neutral #6B7280 · ai #7C3AED. CVD mode keeps the up / down swap.
U4.3 Rules: 60-30-10; no coloured card backgrounds (colour only on chips 10–15 % tint, numbers, buttons); one accent (brand) for
     actions / links; no gradients, glows or dot backdrops behind data (UX-12/13 backdrop off; never behind text in any palette);
     elevation by surface steps; tabular figures; key number large + high contrast, labels small + muted. Extra accents mapped to
     brand / semantic tokens (tokens kept). CI: text ≥ 4.5:1 on every surface; buttons / chips / meaningful borders ≥ 3:1; disabled
     ≥ 3:1 with a visible reason.
U4.4 Proof: colour_v2 vs colour_v3 side by side — Today (Morning), Terminal (chain + basket), Screener (bottom bar Run), My trades
     (positions with P&L), Signals; phone + desktop, dark + light; + the contrast table.

U5 — SCREENER + CHART + TELEGRAM (UI parts of SCR-FIX)
U5.1 "Show on chart" = the Charts page component (EmbeddedChartV2 / ChartV2: up / down colours, axes, volume, zoom, crosshair,
     timeframe shown), scrolled to the matched bar, overlays (range box, levels, markers), "Open in Charts" button; matched bar's time
     + O/H/L/C beside the chart; the Telegram image rendered from the same chart. Replay controls hidden when unavailable.
U5.2 Telegram connects IN PLACE wherever a Telegram action needs a link (Screener evidence, alerts, signals, Copilot, approvals,
     Journal): the existing one-tap connect (TelegramConnectV2 / platform_bot: one-time code → t.me/<bot>?start=<code>) in a small
     sheet; after Start the page detects the link (poll ≤ 2 min or on focus) and completes the original action. No token / chat id typed
     by a user. Machine reasons → the right action (Connect / Unblock / Retry), never a dead sentence; bot not configured → button
     hidden, admin console says so (key NAMES only).
U5.3 Results board: sortable columns, column picker, CSV export, "Auto-refresh 1m" switch, "Run on this watchlist" chip; "N matched ·
     M could not be read" with grouped reason + one action; "Why it matched" with the true OR branch ✅, others grey.

U6 — OI VIEW IN THE V2 APP (components already built: OiBanner, OiHistoryTable, StrikeOiChart, OiSettingsCard, OiAlertsCard — mounted
     only on the old OptionChainPage.tsx:116; OptionChainPageV2 imports none)
U6.1 "OI" view / tab beside Chain / Charts in OptionChainPageV2 (and the Terminal chain panel): banner card (expiry + days left, state
     chip, Put / Call state, PCR value + label chip, "needs history" note on the first snapshot); history table Time | Total Call OI |
     Total Put OI | Diff (Put − Call) | Δ Diff | Signal, newest first, 5 / 10 / 15-minute views; OI by strike chart; settings + alerts
     behind ⓘ / a sheet. Restyle to v2 primitives; English only.
U6.2 One tap "Start following" per underlying (NIFTY / BANKNIFTY / SENSEX), stored per user; "collecting since HH:MM" + data age;
     amber "stale" chip.
U6.3 Colours per U2.1: Diff / Δ Diff Signed; Signal chip Bullish green / Bearish red / Weakening amber / Neutral grey; PCR level chip;
     tabular numbers; phone = stacked rows or sticky Time column (the Trade table clips Diff on phone — do not copy).
U6.4 Today: one compact OI row per followed index (PCR chip + state chip + "Open OI" link).

U7 — BEYOND THE BEST (each derived from a cited user complaint about the best-in-class tool; UI only; behind flags; one PR each)
U7.1 Option chain: "Lots / Qty" unit toggle in the column menu + "ATM ±N" strike-range chip row above the table; spot header shows
     signed Δ and %; IV percentile card under More.
U7.2 Builder: payoff panel row "Exit when basket P&L ≤ −₹X / ≥ +₹Y" that writes the stop into the ticket before placing (uses the
     existing CX engine).
U7.3 Alerts tab: every alert with its symbol chip and an "orphaned" badge when the symbol leaves the list; no cap.
U7.4 Deploy card: the exact F&O contract it will trade ("NIFTY 24500 PE · 03 Nov") beside the status chip; deploy preview before
     Deploy (expiry, strikes, lots, credit, max loss, the user's own SL / target in ₹, product).
U7.5 Order ticket: "Required ₹ / Available ₹" pair recalculated on every lot tap; margin estimate on PAPER via the broker's margin API
     when the adapter has it, else one honest line.
U7.6 Journal: expectancy + avg R tiles, drawdown line, "Export all (CSV)" header button.
U7.7 Copilot chat: CostChip per answer + remaining today; structured rows + Signed where data exists; fold the 7 old tabs into the
     chat jobs under ui_v2, old tabs kept under "Advanced" (nothing deleted).

PROOF OVERALL: after U7, one summary comparison page: every main page before (27 Oct screenshots) / after, phone dark + light +
desktop, with the visible-word count per page before → after and the contrast table.
```
