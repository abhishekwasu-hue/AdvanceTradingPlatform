# UX-1 — self-audit of every page on the 10 UX-2 points (owner order UX-2, 25 Oct)

Docs only. Order: `docs/owner-orders/ATP_ORDER_UX2.md` (with "UX-3 replaced"). Captured from `main` (22b03445 era)
with the e2e mock server (made-up data), `ui_v2` + `colour_v2` ON, dark theme, desktop 1440 and phone 390 - production
is not reachable from the build container. Each point was judged from the screenshot plus a DOM check (native `<select>`
count, the intro paragraph above the first data, `font-variant-numeric` on price cells, skeletons) and a source check.

## Summary (plain words)
ATP's numbers are already in tabular figures on the trading pages and the colours are mostly logical, but four things make
it look older than Kite / TradingView on EVERY page: (1) the header search finds only pages, never a symbol; (2) a
paragraph of explanation sits above the first data on 13 pages ("PageIntro"); (3) browser-default dropdowns
(Underlying, Layout, Symbol, Expiry, timeframe, filters) on 5 pages; (4) nothing feels live - no tick flash, no stale
badge, almost no skeleton loaders. There is no density setting and the Terminal has no watchlist panel yet.
Counts over 17 pages: pass 33 · fail 103 · not applicable 34.

## The table (P pass · F fail · – not applicable)
Points: 1 symbol search · 2 watchlist · 3 type / tabular · 4 no default select · 5 no intro above data · 6 live feel ·
7 density · 8 compact ticket · 9 H1 order · 10 polish / few words

| Page | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | Note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Today | F | F | P | P | P | F | F | – | P | F | 200 words; no watchlist strip on phone; "Your week" is sentences |
| Charts | F | P | F | F | F | F | F | – | F | F | timeframe / candles native select; intro paragraph; watchlist panel exists here |
| Option chain | F | – | P | F | F | F | F | F | F | F | Symbol + Expiry native selects; intro above the title; warning between tabs and data |
| Terminal | F | F | F | F | F | F | F | P | F | F | no watchlist panel (UX-3); Underlying + Layout native selects; intro bar; order panel is a hint until a price is picked |
| My trades | F | – | P | P | F | F | F | – | F | F | 281 words; tabular P&L ok |
| Signals | F | – | F | P | F | F | F | – | F | P | intro paragraph |
| Paper bot | F | – | F | P | F | F | F | – | F | F | 110 words of explanation |
| Find setups | F | – | – | P | F | F | F | – | F | P | not open in the mock (flag) - intro only |
| Strategies | F | – | – | P | F | F | F | – | F | P | intro line |
| AI Copilot | F | – | – | P | F | F | F | – | F | F | 77-word intro before the box |
| Sectors & indices | F | – | F | P | F | F | F | – | F | P | intro line above the heat map |
| Performance | F | – | P | F | F | F | F | – | F | P | 2 native selects; "Aggregated from your full persisted…" developer wording |
| Journal | F | – | F | P | F | P | F | – | F | P | the only page with skeleton loaders |
| News & events | F | – | F | F | F | F | F | – | F | F | 4 native selects; 110 words |
| Settings | F | – | – | P | P | F | F | – | P | P | no density option (UX-8) |
| Broker | F | – | – | P | F | F | F | – | P | P | intro line |
| Plan a trade | F | – | – | P | F | F | F | F | F | P | not open in the mock (flag) |

Global facts behind the columns: search = `src/shell/CommandBarV2.tsx` "Search pages" only (1); the font is Inter
from fonts.googleapis.com in `index.html`, not self-hosted (3); 52 component files still render a native `<select>` (4);
`src/ui/PageIntro.tsx` is used by 13 pages (5); no tick-flash, stale badge or optimistic order state in `src/live` /
the shell (6); no density preference in `src/settings` (7).

## The worst 10 fixes (in order)
| # | Fix | UX step | PR size |
|---|---|---|---|
| 1 | Header search finds symbols first (stocks, indices, F&O "nifty 22700 ce"), recent on top, Enter opens the chart | UX-2 | M (search API + CommandBar) |
| 2 | Remove PageIntro from page tops; its text moves behind the header (?) | UX-6 | S (13 pages, one component) |
| 3 | One shared combobox replaces native selects: Underlying, Layout, Symbol, Expiry, timeframe, filters | UX-5 | M (component + 5 pages) |
| 4 | Terminal watchlist panel, docked left, ON in every preset, linked to chart / chain | UX-3 | M |
| 5 | Live feel: tick flash, header feed dot, stale badge, skeletons for every panel | UX-7 | M |
| 6 | Self-host Inter, `tabular-nums` on every price / qty / P&L, one type scale (live price largest) | UX-4 | S-M |
| 7 | Option chain page order: header → status → cards → chain → reading → tables (OC-1) | UX-10 | M |
| 8 | Today in tiles, fewer words, phone watchlist strip (TD-1) | UX-10 / UX-3 | M |
| 9 | Compact ticket: side, lots, price type, Review; advanced collapsed; review with charges / margin / max loss | UX-9 | M |
| 10 | Density setting Comfortable / Compact in Appearance | UX-8 | S |

Screenshots: the comparison page given to the owner (not committed; made-up data only).
