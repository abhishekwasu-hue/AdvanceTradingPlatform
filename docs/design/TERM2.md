# TERM-2 - Terminal rebuild (owner order 30 Oct, docs/owner-orders/ATP_ORDER_TERM2.md)

Principle (owner): the Terminal owns no copies. It places the components the app already has, linked by ONE active symbol +
expiry. PAPER only; exits never blocked (ADR-0004); AI never places or changes orders (ADR-0006); no platform defaults in order
placement (DEF-0). Behind `terminal`; the current Terminal stays reachable under Settings › Advanced until the owner's ✅.

## 1. Benchmarks

| Product | What we take | Source |
|---|---|---|
| Kite Terminal Mode | A workspace of widgets (chart, watchlist, option chain, orders); ready layouts to start from; resize / remove; saved workspaces | https://zerodha.com/z-connect/business-updates/introducing-kite-terminal-mode , https://support.zerodha.com/category/trading-and-markets/trading-faqs/terminal-mode/articles/what-is-kite-terminal-mode |
| Dhan DEXT T3 | Linked widgets with colour sync; resizable widgets; prebuilt layouts | https://dhan.co/blog/stock-market/dhan-dext-t3-trading-terminal-features/ |
| TradingView | Trade from the chart (orders / positions drawn on the chart); ONE bottom Account Manager with Positions / Orders tabs and a summary row | https://www.tradingview.com/charting-library-docs/latest/trading_terminal/Account-Manager , https://www.tradingview.com/support/solutions/43000766334-chart-trading-on-tradingview-key-features-and-advantages/ |
| Sensibull | Chain -> strategy builder -> payoff -> basket as one chain of steps; payoff as graph or table | https://blog.sensibull.com/category/whats-new-in-sensibull |

## 2. Audit items re-verified (main 124eafc + #708; first report)

| Item | Claim | Result | Where |
|---|---|---|---|
| CT-1 T1 | Terminal chain has no expiry picker | confirmed | terminal/TerminalPageV2.tsx:148 `api.optionChainLive(underlying)` |
| CT-1 T2 | Underlyings fixed to 4 indices | confirmed | terminal/layoutModel.ts:73 `UNDERLYINGS` |
| CT-1 T3 | Tablet 768-1023: layout controls shown while phone tabs render | confirmed | TerminalPageV2.tsx:57 (`NARROW` ≤ 1023) vs :247 (`md:flex` from 768) |
| CT-1 T4 | Exit all only in the Terminal | confirmed | terminal/PanelsV2.tsx:345-361 (`exitAll`); PositionList has no Exit all |
| CT-1 C3 | 3 chain tables | confirmed (3 in v2: ChainTableV2, PanelsV2 ChainPanelV2 :119-233, CockpitDeskV2 `ChainRows` :142; plus legacy v1) | - |
| CT-1 C5 | 2 basket calculations | partly: ONE server calculation (paper/fno_basket.py:124 `evaluate`); TWO frontend renderings of its figures (SpreadBuilderV2 :68-88, BasketPanelV2 `BasketBody` :418-495) | - |
| CT-1 C1 | 2 chart toolbars | confirmed, and one more: 3 in v2 (ChartsPageV2 :257-300 inline, EmbeddedChartV2 :69-80, MultiChartV2 cell :316-325) | - |
| BasketPanelBodyV2 | test-only | confirmed | PanelsV2.tsx:480-506, only terminal.test.tsx imports it |
| Feed chip | a second freshness chip | confirmed | terminal/StatusBarV2.tsx:82-106 (`feedChip`) vs chart/LiveChip.tsx |
| Multi-chart preset | a second multi-chart | confirmed | layoutModel.ts:94-100 (4 fixed symbols, no link groups) vs MultiChartV2 |

## 3. Steps (one PR each)

1. **Context bar** - symbol (the chain page's own index + F&O stock list), expiry (the chain page's own `useOptionChain`: on an
   expiry day the next one, per the user's chain setting), spot with Signed change %, India VIX LevelChip, PCR, DTE, ONE PAPER
   badge. One page-level active symbol + expiry drives chart, chain, order and basket; the link colour is group A of
   MultiChartV2. Fixes T1, T2, T3 (one breakpoint constant `DESKTOP_QUERY`). The status bar drops its own mode chip (one badge).
2. **Panels** - ChainTableV2 + SummaryStripV2 (same as /option-chain), ChartV2 + the compact Charts toolbar with legs /
   breakevens / stops drawn, the BasketPanelV2 body (one rendering of the server's figures), ONE bottom panel Positions · Orders ·
   Alerts with PositionList (Exit / Exit part / Reverse / Roll / Exit all moved into PositionList). Removes ChainPanelV2,
   SpreadBuilderV2's figures, BasketPanelBodyV2, EmbeddedChartV2's toolbar + free-text symbol, the feed chip, the Multi-chart preset.
3. **Layouts** - "Option seller" and "Quick trade" presets, resizable, saved per user (the existing prefs `terminal`); phone tabs
   Chart · Chain · Basket · Positions · Orders; chain tap -> bottom sheet; tablet = phone layout.
4. **R1-R4** - each behind its own flag, PAPER first.
5. **Text** - no paragraph on screen; "?" for explanations.

## 4. Provisional choices (check against DECISIONS; listed in each report)

- Symbol picker: the context bar uses the option chain page's own picker and list (indices + F&O stocks), because the chart
  SymbolPicker lists symbols without options. CT-1 C6 (SymbolPicker everywhere, one universe) unifies both later.
- Old Terminal: kept as `TerminalClassicV2` at /terminal/classic, linked from Settings › Advanced, until the owner's ✅.
- Spot change %: from the Today strip's previous close (NIFTY / BANKNIFTY); other symbols show the spot without a change.

## 5. Step 4 - R1-R4 (each behind its own flag, OFF by default; with `ui_v2` + `terminal`; PAPER first)

| Item | Flag | Built as | Where |
|---|---|---|---|
| R1 Roll | `term_roll` (+ `paper_account`) | "Roll" on a PositionList row / group -> a preview (exit of the legs + the same strikes on the next listed expiry, new legs' credit and max loss from the ONE basket evaluate, roll net) -> one confirm: step 1 the EXISTING exit (Exit now; a basket's legs: the group exit), step 2 the new legs into the one basket for the user to review and place. Not atomic, said on screen; a "Roll half done" notice beside the incomplete-orders notice until the basket is placed or dismissed. A sold leg's stop stays blank (DEF-0). | backend app/paper/fno_term.py `roll_preview`, POST /api/paper/fno/roll/preview; frontend fno/roll.ts, fno/RollV2.tsx |
| R2 What-if | `term_whatif` | `basket/evaluate` with `with_open`: the caller's own open PAPER legs on the same underlying + expiry (at their entry price) plus the new legs, one engine call; the basket draws the combined payoff by default with a "This basket" toggle | fno_term.whatif; BasketPanelV2 |
| R3 Strike to area | `term_strike_area` | the My day read (GET /api/today/my-day, now also open with this flag; adds each option's master strike / right); a chip on the short row and an optional mark under the strike on the chain | fno/strikeArea.ts; ChainTableV2 `areaMarks` |
| R4 Margin pair | `term_margin_pair` | `basket/evaluate` with `margin_pair`: required = the broker's basket margin through his own session where the adapter has it, else ATP's labelled estimate (smart_execution.margin_view); available = the status bar funds (terminal/status.py `funds_of`) | fno_term.margin_pair; BasketPanelV2 |

Provisional choices (step 4): the roll's order is exit first, then the new legs (the basket places hedges first); a roll into a
basket that already holds legs adds to them instead of replacing them; what-if values the open legs at their entry price (the
book's own result at expiry); R3 is wired in the Terminal only (PositionList takes `areas` anywhere).
