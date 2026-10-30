# CT-1 - Charts + Terminal consolidation: audit (owner order 30 Oct, docs/owner-orders/ATP_ORDER_CT_1.md)

Rule: ONE component per kind, linked by symbol; layouts place widgets, they do not re-implement them. TERM-2
(docs/design/TERM2.md) builds the Terminal items (T1-T4, C3 Terminal side + addendum, C5, C7, L2); this note covers the
whole order and is the first report (confirmed / not reproduced) on main d4a8240a.

## 1. Benchmarks

| Product | What we take | Source |
|---|---|---|
| Kite Terminal Mode | Widgets placed in a workspace, not re-built per layout | https://zerodha.com/z-connect/business-updates/introducing-kite-terminal-mode |
| TradingView chart trading | One chart with its toolbar; orders / positions drawn on it; one bottom account panel | https://www.tradingview.com/support/solutions/43000766334-chart-trading-on-tradingview-key-features-and-advantages/ |
| Dhan DEXT T3 | Linked widgets, colour sync | https://dhan.co/blog/stock-market/dhan-dext-t3-trading-terminal-features/ |
| Upstox multi-chart | Up to several charts in one layout, each with its own symbol picker | https://upstox.com/ (Pro web, multi-chart layouts) |

## 2. Items re-verified

| Item | Result | Where (main d4a8240a) | Done in |
|---|---|---|---|
| T1 no expiry picker in Terminal | confirmed | terminal/TerminalPageV2.tsx:148 | TERM-2 step 1 (#714) |
| T2 fixed underlyings | confirmed | terminal/layoutModel.ts:73 | TERM-2 step 1 (#714) |
| T3 tablet controls | confirmed | TerminalPageV2.tsx:57 vs :247 | TERM-2 step 1 (#714) |
| T4 Exit all only in Terminal | confirmed | terminal/PanelsV2.tsx:345-361 | TERM-2 step 2 (#715) |
| C1 chart toolbars / ProChart windows | confirmed: 3 v2 toolbars (ChartsPageV2 :257-300, EmbeddedChartV2 :69-80, MultiChartV2 cell :316-325); ProChart windows still routed (App.tsx:32-36, :338-342) | - | CT-1 PR 3 |
| C2 two multi-charts | confirmed: Terminal Multi-chart preset (layoutModel.ts:94-100) vs MultiChartV2 | TERM-2 step 2 (preset no longer offered) |
| C3 three chain tables | confirmed: ChainTableV2, Terminal ChainPanelV2, cockpit `ChainRows` (fno/CockpitDeskV2.tsx:142) | Terminal: TERM-2 step 2 (ChainLadderV2); cockpit: CT-1 PR 2 |
| C4 bottom strip repeats the summary | confirmed: ChainBottomStripV2 (fno/ChainTradingV2.tsx:193-201) shows PCR / max pain / ATM IV / DTE again under SummaryStripV2 | this PR |
| C5 two basket renderings | confirmed (one server calculation, two renderings) | Terminal: TERM-2 step 2; cockpit: CT-1 PR 2 |
| C6 symbol picking | confirmed: SymbolPicker on Charts / MultiChart / Watchlist; chain page and Paper bot use a Combobox list; several free-text symbol inputs (AlertRulesPanel :112, AlertDialogV2 :70, StrikesSectionV2 :69, RiskPartsV2 :107) | CT-1 PR 4 |
| C7 linked by symbol | confirmed (Terminal had none) | TERM-2 steps 1-2 |
| C8 one PAPER badge | confirmed: PracticeFrame in 8 files, "· Practice" / "practice account" in 4 | Terminal: TERM-2 step 5; rest: CT-1 PR 4 |
| C9 freshness | confirmed: Terminal feed chip (StatusBarV2 :82-106) besides the shell FeedDot | TERM-2 step 2 |
| C10 one Ask AI / Read chart entry | confirmed: chain-page Ask AI (OptionChainPageV2 header), Charts Read this chart (ChartsPageV2 :287) and Analyze a chart (:282), separate code | CT-1 PR 5 (with CP-UX CU4) |
| L1 Charts phone tabs / toolbar | confirmed: hub tabs + a Multi-chart tab (ChartsPageV2 :103-113); the full toolbar wraps on a phone | CT-1 PR 3 |
| L2 Terminal phone tabs / presets / bottom panel | confirmed | TERM-2 step 3 (#718) |
| L3 long texts | confirmed (Terminal texts; Charts intro ChartsPageV2 :231) | Terminal: TERM-2 steps 2 + 5; Charts: CT-1 PR 3 |

## 3. Plan (one PR per group, comparison page per PR)

1. This PR: the audit + C4 (the bottom strip keeps only the ATM button and the basket badge).
2. C3 / C5 cockpit: the F&O desk places ChainLadderV2 and the BasketPanelV2 body (after TERM-2 step 2 merges).
3. C1 + L1: one ChartToolbar with full / compact modes; the ProChart windows' entry points open ChartV2; phone toolbar.
4. C6 + C8: SymbolPicker everywhere (one universe); one PAPER badge per screen app-wide.
5. C10: one "Ask AI / Read chart" entry in the shell (with CP-UX CU4).
