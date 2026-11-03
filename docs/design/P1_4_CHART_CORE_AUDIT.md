# P1-4 chart core audit (J2, order row 18 Parts 1-2)

Scope: every place in `frontend/src` that draws a price chart, and whether it goes through the one chart core.
Method: grep for `lightweight-charts`, `createChart(`, `new ChartCore`, `<ChartV2` / `<EmbeddedChartV2` /
`<MultiChartV2` / `<ProChart`, `<canvas`, `<polyline`, `Sparkline`, `recharts` (not a dependency). Line numbers are
from the commit this file was added in.

## The core

- `src/chart/chartCore.ts:148` `ChartCore` - the only `createChart` call outside ProChart (`chartCore.ts:188`);
  options from the shared builder `src/chart/chartOptions.ts`; gestures / view state in `src/chart/gestures.ts`
  (`PriceDrag`, `INITIAL_VIEW`, `latestVisible`, `shiftWheelPosition`); older bars near the left edge
  (`chartCore.ts:223`).
- `src/chart/ChartV2.tsx:109` - the React wrapper (`new ChartCore` at `:129`). Auto / Reset / Latest =
  `ChartViewControls` (`ChartV2.tsx:248`, prop `viewControls`, default on, `:63`); one-finger vertical pan on touch =
  `captureTouch` (default off, `:66`, applied `:150`).
- Thin hosts over ChartV2: `EmbeddedChartV2.tsx:112`, `MultiChartV2.tsx:343`, `BacktestChartV2.tsx` (`<ChartV2`),
  `ChartsPageV2.tsx:298`, `screener/EvidenceChart.tsx:163`.
- The second implementation: `src/components/ProChart.tsx` (`createChart` `:301`, own `LightweightEngine` `:342`).
  It shares only the option builder (`baseOptions` `:85-103` spreads `baseChartOptions()`); it has no Auto / Latest
  controls, no vertical price pan (no `PriceDrag`), "Fit all candles" instead of Reset (`:526`).
  `CandleChart.tsx` no longer exists.

## Pages and components that draw a price chart

| Page | File | Uses core? | Missing controls | Fix size |
|---|---|---|---|---|
| Charts page, Chart tab (v2) | `chart/ChartsPageV2.tsx:298` | yes | none; `captureTouch` only in full screen (`:300`) | - |
| Charts page, Multi-chart tab (v2) | `chart/MultiChartV2.tsx:343` | yes | controls only on the active cell (`viewControls={active}`, by design) | - |
| Signals inbox detail (v2) | `signals/SignalPartsV2.tsx:148` -> EmbeddedChartV2 | yes | none | - |
| Copilot answer chart (v2) | `copilot/chat/AnswerV2.tsx:161` -> EmbeddedChartV2 | yes | none | - |
| Copilot chart workspace (v2) | `copilot/workspace/ChartWorkspaceV2.tsx:98` -> EmbeddedChartV2 | yes | none | - |
| In-place chart panel (v2) | `inplace/panels/ChartPanel.tsx:10` -> EmbeddedChartV2 | yes | none | - |
| Terminal chart panel (v2) | `terminal/PanelsV2.tsx:87` -> EmbeddedChartV2 | yes | none | - |
| Strategy builder backtest (v2) | `strategyFlow/StrategyFlowV2.tsx:38` -> BacktestChartV2 | yes | none | - |
| Test a strategy, `ui_v2` on | `pages/BacktestPage.tsx:653` -> BacktestChartV2 | yes | none | - |
| Scanner evidence chart | `screener/EvidenceChart.tsx:163` | yes | Auto / Reset / Latest hidden in `compact` (`viewControls={!compact}`); compact is used in the result list (`screener/ResultViews.tsx:78`, `h-64`) | 1 prop (owner choice) |
| Test a strategy, `ui_v2` off | `pages/BacktestPage.tsx:654` | **no** (ProChart) | Auto, Latest, vertical pan | legacy, frozen |
| Contract / spread window `?contract=` | `pages/ContractChartWindow.tsx:85` | **no** (ProChart, both flag states) | Auto, Latest, vertical pan | medium (see below) |
| Chart window `?chart=` | `pages/ChartWindow.tsx:117` | **no** (ProChart) | Auto, Latest, vertical pan | none needed: redirects to the Charts page under `ui_v2` (`:43-50`) |
| Chart workspace `?workspace=1` | `pages/ChartWorkspace.tsx:294` | **no** (ProChart) | as above | none needed: redirects under `ui_v2` (`:57-58`) |
| Legacy Signals page | `pages/SignalsPage.tsx:178` | **no** (ProChart) | as above | legacy only (`routes.ts:16`; v2 = `signals/SignalsV2`) |
| Legacy Positions chart card | `components/PositionChartCard.tsx:92` | **no** (ProChart) | as above | legacy only (`routes.ts:28`; v2 Positions links to `/charts`) |
| Legacy Dashboard market pulse | `components/MarketPulseCard.tsx:30` | **no** (ProChart, compact) | as above | legacy only (`routes.ts:11`; v2 = `dashboard/DashboardV2`) |

Not candle / price-axis charts (out of the core's scope; static SVG, no pan or zoom by design):
`ui/Sparkline.tsx` (index strip `dashboard/MarketStripV2.tsx:67`, equity in `JournalPage.tsx:155`,
`StrategyFlowV2.tsx:598`, score in `rhythm/SectionRowsV2.tsx:466`), option-chain analytics (`optionChain/ChartsViewV2.tsx:313`,
`AnalysisTabsV2.tsx:257`, `AskResultV2.tsx:120`), payoff thumbnails (`optionsBuilder/TemplateGallery.tsx:20`),
basis lines (`pages/FuturesBasisWindow.tsx:31`) and OI bars (`ContractChartWindow.tsx:23`).

## Counts

- Price-chart call sites: 17. Through the core: 10 (one hides the controls in compact mode). Bypassing: 7, all ProChart.
- Bypasses reachable with `ui_v2` on: 1 - `ContractChartWindow` (`?contract=`), for a spread and old links
  (`App.tsx:332-335`). The other 6 are `ui_v2`-off paths or redirect away under `ui_v2`.

## Conversion in this pass: none

- `ContractChartWindow` is the only v2-reachable bypass. Same data (daily candles from `toCandles`), but ChartV2's
  props do not cover what ProChart gives this window today: the indicator toggles (EMA, SMA, BB, VWAP, Supertrend,
  RSI, ADX - `ProChart.tsx:514-529`), the Strategies panel, the header with last close and change (`:541-545`) and the
  "Fit all candles" button. ChartV2 offers only four overlays and the volume / RSI panes, with no toolbar of its own.
  A move now removes features, which CLAUDE.md §8 does not allow without Abhi's approval. A spread without OHLC is
  also drawn as flat candles today (`charting/contracts.ts:68-69`); a line would read better but changes behaviour.
- The six legacy paths: the chart review (`docs/design/CHART_COLOUR_REVIEW.md` R1, rows 38-45) freezes ProChart for
  `ui_v2` off and removes it with the legacy shell; converting them would rewrite pages that are going away.

## Open for P1-4 (from the order, not verified as done by grep)

- Embedded chart "tap to activate" + first-time hint (2.3): no code found (`grep "Tap to move"` = 0).
- Visible-range memory per symbol + timeframe (2.5): not found (`chartsPage.ts:126 saveView` stores the view type /
  overlays, not the range).
- Gesture e2e: `e2e/chart-axes-v2.e2e.ts` covers vertical drag -> Auto off, sideways drag -> Latest, price-axis drag /
  double-click, Reset, Shift + wheel, readout. No pinch, long-press or embedded page-scroll test found.
- Phone recording: not in the repo.

## Owner decisions needed (defaults)

1. Contract / spread window under `ui_v2`: default - keep ProChart until the v2 chart has an indicator menu for
   standalone windows; then move it with the same feature list.
2. Scanner result-list chart (compact): default - keep the controls hidden (small preview; the full evidence chart has
   them).
