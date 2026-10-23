From Abhi — CHART INTERACTION (both-axis scrolling, reset, phone gestures) + PURPOSEFUL MULTICOLOUR DESIGN (ATP blue stays) — design + coding order (English)

Record first: docs/owner-orders (INBOX verbatim, a DECISIONS line: "the brand accent stays ATP blue; colour by meaning", README row 18). Independent review of this order against the code before building (CLAUDE.md §6). Behind ui_v2. Keep what is built; extend, don't rewrite (§8). One draft PR per part; tests; before / after screenshots AND short screen recordings of the gestures (360 / 390 phone, 1440 desktop, light / dark).

What Abhi reported: chart scrolling is not proper (no free movement on both axes, no clear reset); the app looks flat — he wants colour used the way modern broker apps do (reference: a broker app screenshot with one strong accent on the main action, outlined secondary buttons, green / red values, a yellow range bar, bold headings). Do NOT copy any broker's look or colours; ATP keeps its own BLUE accent.

FOUND IN THE CODE (confirm in the review)
- lightweight-charts 5.2.x. Options in src/chart/chartCore.ts (and duplicated in src/components/ProChart.tsx): handleScroll.vertTouchDrag = false (a vertical drag on a phone scrolls the page, never the chart); the price scale is always auto-scaled, so a vertical pan is impossible; reset exists only as axisDoubleClickReset (unknown to users, awkward on phones).
- Three chart implementations: src/components/CandleChart.tsx, src/components/ProChart.tsx, src/chart/chartCore.ts (ChartV2 / MultiChartV2 / EmbeddedChartV2); used by Charts, ChartWindow / ChartWorkspace, ContractChartWindow, PositionChartCard, MarketPulseCard, SignalParts, SignalsPage, ChartStrategies. Behaviour differs per page and a fix in one does not reach the others.
- ProChart already loads older bars near the left edge (subscribeVisibleLogicalRangeChange / nearStart); check whether chartCore does.
- Colours: tokens in src/styles/tokens.css (brand blue, up green, down red …), enforced by scripts/check-v2-colours.mjs; palette generator scripts/gen-palette.mjs.

PART 1 — ONE CHART CORE
1.1 Make src/chart/chartCore.ts the single interaction core. CandleChart and ProChart keep their props / features but delegate creation, options, gestures, toolbar and data paging to the core (no feature lost; each page's existing tests stay green). One options object, one test suite for behaviour.

PART 2 — INTERACTION (TradingView / Kite-like)
2.1 Both axes:
 - drag on the plot = pan time AND price (vertTouchDrag on; dragging vertically switches the price scale out of auto mode, as TradingView does);
 - drag on the price axis = stretch / compress price; drag on the time axis = zoom time; mouse wheel = zoom time around the cursor; Shift + wheel = pan time; pinch = zoom (phone / trackpad).
2.2 Controls (a small floating toolbar, bottom-right of the plot, 44 px targets on phones, tooltips on desktop):
 - "A" Auto price scale toggle (on = fits visible candles; turns off automatically on a manual vertical move; tap to return);
 - "Reset" (auto price + default zoom + scroll to the latest bar);
 - "→ Latest" arrow, shown only when the latest bar is off-screen;
 - optional: "Log", "%" price scale and "Invert" in the chart settings popover (ChartSettingsPopover).
 Double-click / double-tap on an axis still resets that axis.
2.3 Phone gestures without fighting the page:
 - full-screen chart (the Charts page, ChartWindow, and a new "expand" button on every embedded chart): the chart captures all touches (CSS touch-action: none on the plot);
 - embedded chart inside a scrolling page (cards, signals, positions): one-finger vertical swipe scrolls the PAGE; the chart responds to horizontal drag, pinch, or after a tap that "activates" it (a subtle outline; tap outside or 3 s idle deactivates) — like map embeds; a hint the first time ("Tap to move the chart");
 - long-press = crosshair with the OHLC / volume readout (drag moves the crosshair, lift ends it); a normal drag = pan;
 - landscape rotation on the Charts page = full-screen chart.
2.4 History: scrolling to the left edge loads older bars automatically (a small "loading" mark at the edge; stops at the start of available data with a plain "No older data"); the view does not jump when bars are prepended or when a live bar updates (keep the visible range — already done in places; make it core behaviour).
2.5 Memory: the zoom (visible range) and price-scale mode are remembered per symbol + timeframe per user device (localStorage, try / catch, safe when empty), restored on open; "Reset" clears it.
2.6 Multi-chart: the time cross-hair and scroll can be linked across panes (a toggle, default on for the same symbol).
2.7 Performance: 60 fps pan on a mid-range phone with 5,000 bars; no layout shift when the toolbar appears.
2.8 Tests: unit tests on the core's option builder and gesture state (auto-scale off after a vertical pan; Reset restores; Latest arrow visibility; memory save / restore; prepend keeps the view); Playwright gesture tests (drag both axes, pinch, long-press crosshair, embedded-chart page scroll vs activated chart) on phone and desktop viewports.

PART 3 — PURPOSEFUL MULTICOLOUR (ATP BLUE stays the brand)
3.1 Colour by meaning — add these semantic tokens (light + dark, WCAG AA contrast for text, 3:1 for UI parts), generated through scripts/gen-palette.mjs and allowed by check-v2-colours:
 - brand (blue, unchanged) = the ONE main action per screen (Place order, Start, Approve, Connect) as a solid button; every other action is an outlined / ghost button;
 - up (green) / down (red) = prices, P&L, Calls-vs-Puts change; colour-blind mode (blue / orange) stays;
 - warn / range (amber-yellow) = warnings, ranges (52-week bar, day range, IV range), "renew today";
 - info (teal / sky) = information chips, tips, "What is this?";
 - ai (violet) = AI features only (Copilot, Vision, Connect AI, AI chips) — a recognisable AI colour;
 - paper (orange) = the PAPER / practice badge everywhere;
 - neutral surfaces: a deeper navy-slate dark theme with layered surfaces (surface-1/2/3) so cards separate without borders everywhere.
3.2 Where colour appears (and where not): price / P&L values; status chips (Connected = green, Needs action = amber, Error = red, Info = teal); filter chips (selected = brand outline + tint, others outlined); nav group icons tinted per group (Daily / Markets / Strategies & AI / Review) in a muted tone; range bars (52-week, day range) as a coloured track with a marker; charts and the chain use a fixed data palette (call OI, put OI, IV, PCR, payoff profit / loss areas). Large background fills, gradients and many colours on one card are NOT allowed (calm principle).
3.3 Typography with colour: bold white / near-black headings, secondary text muted, numbers in tabular figures; the main number of a card larger than its label.
3.4 Rollout: tokens first (one PR), then the shared components (Button primary / outline / ghost, Chip, StatusChip, RangeBar, Badge), then pages adopt the components — no page-by-page custom colours. Update the v2 colour check to forbid raw hex / Tailwind palette colours outside tokens (already the rule) and to flag more than one solid brand button per screen in the e2e snapshots (a heuristic warning).
3.5 Screenshots for Abhi BEFORE rollout: Today, Option chain, Signals, Positions, Charts, Settings — light and dark, phone and desktop — in a single comparison page (before / after), for his approval of the look.

ORDER
1. Review. 2. Part 1 (one core) + Part 2.1–2.2 (both axes, Auto, Reset, Latest) — the biggest pain. 3. Part 2.3–2.8. 4. Part 3: tokens → components → the comparison screenshots to Abhi → page adoption after his OK.
Reports to Abhi in Marathi.
