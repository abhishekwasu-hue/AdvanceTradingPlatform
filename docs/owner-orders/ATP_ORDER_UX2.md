# Owner order UX-2 — modern trading-app look (25 Oct, verbatim)

OWNER ORDER — UX-2: ATP still looks dated next to Kite / TradingView-class apps (owner, 25 Oct). This lifts the M38 UI freeze for UX-2 + UI-H page waves; trader-function items (M38 PR-3) keep priority where they conflict; max 3 in progress. Research R24. ≤25-line reports; one comparison page per step (phone + desktop, light + dark).
UX-1 Self-audit first (no code, today): score every page on these 10 points (pass / fail + screenshot) and list the worst 10 fixes.
UX-2 Symbol-first search: the header search finds symbols first (stocks, indices, F&O contracts — type "nifty 22700 ce"), then pages; Ctrl/⌘ K opens it; recent symbols on top; Enter opens the chart.
UX-3 Watchlist rail on desktop (always visible, collapsible, Kite-style): the user's lists + system lists, live LTP and change with arrows, tap → chart, hover → buy/sell/chart buttons; on phone a watchlist strip on Today.
UX-4 Typography: one product font with tabular figures (Inter or IBM Plex Sans, self-hosted), `tabular-nums` on every price/qty/P&L; type scale — live price largest and highest contrast, secondary metrics smaller and muted; consistent decimals (2 for prices, Indian grouping).
UX-5 Replace every browser-default <select> (Underlying, Symbol, Expiry, Layout, Candles…) with the shared combobox: type-to-filter for symbols/expiries, recent first, grouped, keyboard, bottom sheet on phone; short fixed lists may stay a styled segmented control.
UX-6 Remove "About this page" blocks from page tops — page help lives in the header (?) only; no paragraph above the first data.
UX-7 Live feel: subtle tick flash on price change, a connection/feed status dot in the header, stale-data badge when quotes stop, skeleton loaders instead of blank panels, optimistic order states (sending → placed → filled).
UX-8 Density setting (Settings → Appearance): Comfortable / Compact (tighter rows, smaller paddings) — traders choose.
UX-9 Order ticket: default = side, qty/lots, price type, Review; advanced (SL-M, product, validity, exits) collapsed; review shows price, charges estimate, margin, max loss; phone LIVE confirm = press-and-hold (PAPER: tap).
UX-10 Resume UI-H page waves (H1 order: header → badge → cards → visual → details) page by page: Today, Charts, Option chain, Terminal, My trades first.
Proof: the UX-1 table today; then each step's comparison page.
