From Abhi — Screener addendum U5: UI design direction for the Screener and Scan Settings (English). Applies to SCREENER_SPEC §3, U2 (builder), U4 ("Then" panel). The page must feel like a precision instrument a trader is proud to open every morning — not a form. Work inside the existing design system (tokens.css, data-theme dark/light, colour-blind up/down variant, reduce-motion flag, Inter + JetBrains Mono); extend tokens, do not fork them. Load the repo's frontend conventions first; ship Storybook/visual-regression stories for every new component.

1. Design concept — "the funnel you can see"
 The one memorable element: the condition canvas is drawn as a live funnel. The universe enters at the top as a count; every block is a stage that narrows the stream, and a thin numeric trail shows how many symbols survive each stage, in real time, as the user edits. Groups (ALL/ANY/NOT) are visible as bracketed lanes, not nested grey boxes. When a stage kills the result, its trail turns to the warning colour and the stage offers "see what this removes". Everything else on the page is quiet so this one element carries the character.

2. Tokens (extend tokens.css; all with dark and light values and the colour-blind variant)
 - Surface scale: page, panel, raised, inset — four steps only, with 1 px hairlines from the existing border token; no drop shadows on panels (shadow only on floating menus and the drag ghost).
 - Semantic colours already present (up/down, warn, info) plus two new ones: `--signal` (the funnel's live trail and "matched" state; a restrained cyan-teal in dark, deep teal in light) and `--armed` (auto-execution armed state; amber-orange, used only for armed/LIVE indicators so it stays meaningful).
 - Radii: 6 px for controls, 10 px for panels; nothing else.
 - Type scale (Inter for UI, JetBrains Mono only for numbers/prices/query text): 12 / 13 / 15 / 18 / 24 / 32 with intentional weights (400/500/600); numbers in tabular figures; no all-caps labels; sentence case everywhere.
 - Spacing: 4-pt grid; dense but breathing (panel padding 16, row height 36, canvas stage height 44).
 - Motion: one orchestrated moment — when a scan runs, the funnel trail fills top-to-bottom (≤ 600 ms) and the result board settles. Hover/expand/confirm motions answer user actions only; reduce-motion disables all.

3. Layout (desktop ≥ 1280: three columns 280 / flex / 380; tablet: canvas + collapsible drawers; mobile: stacked with a sticky bottom bar showing live count + Run)
 - Header strip: scan name (inline editable), mode chip (Notify / One-click / Auto PAPER / Auto LIVE armed with `--armed`), data freshness pill with timestamp, Run / Save / Alert / Share buttons (primary = Run).
 - Left rail "Universe": picker with chips (index, sector, F&O, watchlist, custom algebra), timeframe set, schedule, as-of date; the total count sits at the top of the funnel.
 - Centre "Conditions" (the funnel canvas): stages as full-width rows with a category glyph, timeframe chip, plain-English summary, inline parameter editing (click the number, edit in place), advanced fold; drag handle; disable toggle; duplicate; group brackets drawn as a continuous left edge with ALL/ANY/NOT labels; the survivor trail on the right edge of every stage.
 - Right "Results": a result board with view switch — table (dense, sortable, column presets, sparklines), heatmap (treemap by sector sized by market cap or by matched-score), chart grid (12/20 mini charts with the scan's own levels drawn), RRG, F&O dashboard. Row hover reveals pass/fail chips per stage ("why it matched"). Row actions: chart, watchlist, alert, trade, ask Copilot.
 - "Then" panel (U4) docks under Results as a collapsible drawer; arming state is a persistent chip in the header, never a hidden setting.
 - Template gallery: a full-screen sheet with real preview thumbnails rendered from live data (not icons), grouped by category, each card showing last run count and a one-line definition.

4. States and copy (plain, sentence case, user vocabulary)
 - Empty canvas: "Start with a template or add your first condition." with the two actions inline.
 - No results: the killing stage highlighted; "This condition removes every symbol. Loosen it or disable it to see candidates." with buttons.
 - Stale data: freshness pill turns warn; banner "Data is behind by 14 min — results may be outdated; alerts are paused." (time is live, never hardcoded).
 - Alert created: toast "Alert on — Telegram, email" naming the channels.
 - Armed: header chip "Auto LIVE armed until Fri 15:30" with disarm one click away; disarm confirmation explains that protective exits stay active.
 - Errors describe the fix: "RSI needs a timeframe — pick one for this condition."
 - Never "Submit", never system names (no "webhook config" to a trader — "Send to another app").

5. Components to build (each with story, tests, keyboard support, focus ring, ARIA)
 FunnelCanvas, StageRow (per block type renders its form), GroupBracket, SurvivorTrail, ParamInline (numeric/select/time/TF editors), TimeframeChip, UniversePicker (with set-algebra chips), ResultBoard (Table / Heatmap / ChartGrid / RRG / FnO views), WhyMatchedChips, FreshnessPill, ModeChip + ArmBanner, ThenDrawer (trade template form), TemplateSheet, NLBox (shows generated stages before applying), QueryDrawer (two-way ScreenQL editor with highlighting and inline errors), AlertComposer (channels, cooldown, digest, quiet hours), DeliveryLog.

6. Quality bar
 - Visual regression (Playwright screenshots) for dark, light, colour-blind, mobile; Lighthouse accessibility ≥ 95; keyboard-only build of a full scan possible; reduce-motion verified; no layout shift on data refresh; 60 fps canvas interactions with 50 stages.
 - Design review checklist per PR: one memorable element (the funnel) and nothing competing; no decorative gradients, no identical-card grids, no shadows on static panels, no all-caps, numbers in mono tabular, copy in sentence case, every state designed (loading, empty, error, stale, armed).
 - Screenshots of every view attached to the PR; a short "design notes" section explaining choices.

7. Order: D1 tokens + FunnelCanvas + StageRow (indicator block) + ResultBoard table → D2 remaining block forms + GroupBracket + SurvivorTrail live counts + why-matched → D3 heatmap/chart grid/RRG views + TemplateSheet with live thumbnails → D4 ThenDrawer + ModeChip/ArmBanner + AlertComposer + DeliveryLog → D5 mobile, accessibility, visual regression suite.
