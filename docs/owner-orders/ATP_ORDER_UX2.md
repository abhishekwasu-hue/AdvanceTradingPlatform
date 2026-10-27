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

## UX-3 replaced (owner, 25 Oct, verbatim)

UX-3 (replaces the earlier UX-3): the watchlist is a Terminal panel, docked on the left and ON in every default layout (collapsible, resizable, saved with "Save as mine"): the user's lists + system lists, live LTP and change with arrows; clicking a symbol switches the Terminal's underlying/chart/chain (linked panels); hover → Buy / Sell / Chart; add/remove/reorder; a "+ add symbol" box using the symbol-first search. Other pages do not get a permanent rail (phone keeps a watchlist strip on Today).

## Terminal addendum (owner, 25 Oct, verbatim)

Terminal addendum to M39 (same PR). ≤10 lines.
TA-1 Chain panel: CALLS | PUTS group header over the two sides; expiry selector inside the panel (next weekly default per the user's settings); ATM row shaded and centred on open, ITM sides lightly shaded; OI as an in-cell bar.
TA-2 Empty panels shrink: when Positions / Orders / Spread builder are empty they collapse to one line ("No open positions") and the chart takes the space; they expand when content arrives.
TA-3 Panel help text (Spread builder, Order) moves behind (?) — one short line max on the panel.
TA-4 Default Option-seller layout = Watchlist (left, UX-3) · Chart (largest) · Chain · Order (compact), Positions + Orders as tabs in one bottom panel.
