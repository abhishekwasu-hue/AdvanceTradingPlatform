# Part CH - Advanced charting: design note and plan

Source: `docs/specs/ATP_CHARTING_SPEC.md` (Abhi). The MASTER SPEC working rules and `docs/WORKING_RULES.md` (#86)
apply:
- don't stop; multitask; self-review every diff before it is pushed;
- draft PRs only, no LIVE;
- a test with every change;
- no hardcoded dates or prices.

## What exists (main fd217a4)
| Piece | Where | Becomes |
|---|---|---|
| `ProChart` (483 lines, lightweight-charts ^4.2.3): candles, volume, EMA/SMA/BB/VWAP/Supertrend overlays, RSI/ADX panes, timeframe pills, live LTP, lazy older history, `priceLines`/`zones`/`markers` props, strategies on chart (Phase AO) | `frontend/src/components/ProChart.tsx`, used by 5 files (Signals, Backtest, ChartWindow, PositionChartCard, MarketPulseCard) | The **embedded/mini implementation** of `ChartEngine` (CH1). Its props become calls on the interface; existing pages keep working |
| `CandleChart` (mini), `EquityCurveChart`, `PositionChartCard` | `components/` | Mini charts stay on lightweight-charts. EquityCurve is not a market chart and stays outside the interface |
| `ChartWindow` (pop-out page), `chartHistory`, `chartHelpers` | `pages/`, `components/` | The pop-out of the multi-chart workspace (CH5); history loading moves into the datafeed adapter |
| Backend: market_data candles (broker), lake history API (B5, as-of, adjusted), price_action engine, option chain + Greeks, instruments + `nse_expiries` | `backend/app/...` | The UDF-style datafeed and the platform layers (§2). One computation library for chart, screener and backtest (CH6) |

## The abstraction (ADR-0023)
`frontend/src/charting/engine.ts` defines one interface, `ChartEngine`:
- symbol and timeframe: `setSymbol`, `setTimeframe`;
- bars: `setBars`, `appendBar` (live);
- studies: `addStudy`, `removeStudy`;
- drawings: `addDrawing`, `updateDrawing`, `removeDrawing`;
- layout: `serialize`, `deserialize`;
- `setLayer(id, LayerData)` for the platform overlays;
- events: crosshair, click, drawingChanged, visibleRangeChanged.

Implementations:
- **B-lite**: today's ProChart behind the interface (CH1);
- **B**: lightweight-charts v5 plugins/primitives with our drawing core (CH2);
- **A**: the TradingView Advanced Charts adapter, once access is granted (CH7).

Above the interface, and therefore engine-agnostic:
- drawings storage;
- layers (price action, options, trades, screener hits);
- chart actions (alert, find similar, ask Copilot, PAPER proposal, snapshot);
- layouts and link groups.

Switching engines never loses user data: drawings are stored in our own JSON schema (time/price anchors) and each
engine maps it in and out.

## Rules the build keeps
- **Every number shown is from data, never from pixels.**
  - Layers come from backend endpoints with an `as_of`.
  - "Ask Copilot about this chart" sends the chart state (symbol, timeframe, visible range, drawings, layers) to the
    agent's tools, never a screenshot to be read.
- **No action places an order.**
  - Dragging an SL or target line updates a *proposal* (monitor state machine, ADR-0006).
  - "Propose trade from drawing" creates a PAPER proposal.
- **Replay shows no future data.**
  - The backend recomputes the layers as of each replayed bar.
  - The test: the layer at bar N never changes when bars after N are added or altered (it is also the look-ahead test
    of the price-action engine).
- **Option and spread charts are computed server-side** with the one Greeks/IV library that backtest and risk use.
  - The synthetic spread series is the sum of its legs, with sign and quantity.
  - Expired contracts stay chartable from the lake.
- **User indicators are ScreenQL expressions** (part S, ADR-0021). There is no Pine and no arbitrary JS.

## Order of work (one draft PR per step, about 800 lines at most; big steps split)
| Step | Content | Depends on |
|---|---|---|
| CH0 | This note, ADR-0023, spec stored | - |
| CH1 | `ChartEngine` interface + ProChart refactor behind it (no visible change); drawings storage API + migration (`chart_drawings`: user, symbol, anchors JSON, version, lock, timestamps; undo/redo is client-side over versions); export/import JSON; tests | - |
| CH2 | lightweight-charts 4 -> 5 upgrade; primitives drawing core (trendline, ray, horizontal/vertical, parallel channel, rectangle/zone, Fibonacci retracement/extension, text, long/short position, measure); save/load, undo/redo, lock, cross-timeframe sync (anchors are time/price) | CH1 |
| CH3 | Price-action layer endpoint (as-of) + rendering + toggles + debug mode; replay with as-of recompute | CH1, the price_action engine |
| CH4 | Option-contract and spread charts (synthetic series, OI/IV/Greeks panes, IV/PCR/max-pain/OI-by-strike/basis/participant/rollover charts, option symbol search, chain -> chart linking); options and trades layers; chart actions | CH1, part B lake, U1-c (lots, ban), S3 Notification Service for alerts |
| CH5 | Multi-chart workspace (1/2/4/6/8), link groups, layouts, shortcuts, chart types (Heikin Ashi, Renko, range, line/area, hollow), session breaks, 24x7 axis | CH2 |
| CH6 | Server-side study library parity (ScreenQL studies on charts), WebWorker for client math, performance tests (50k bars < 1 s) | S1 (ScreenQL) |
| CH7 | Track A adapter (UDF datafeed, custom studies, layer bridge) behind a per-tenant flag; A/B parity tests against B | TradingView access (Abhi) |

**Place in the MASTER order** (CH-1, provisional): CH0 now. CH1 and CH2 can run beside H-C2/S1 because they are
frontend plus a small table. CH3 follows. CH4 waits for the lake (part B) and U1-c.

## CH1a (built): drawings storage
- **Schema `drawing/1`** (`app/charts/drawings.py`), our own and engine-neutral.
  - Kinds: trendline, ray, measure, rectangle, fib retracement and extension, channel, hline, vline, text,
    long/short position.
  - Anchors are time/price only. Each kind has its rule: hline is price-only, vline is time-only, a position is an
    entry plus price-only stop and target.
  - Style: colour hex, width 1-6, line style, extend.
  - Times are stored in UTC. Text may not contain `<` or `>`.
- **Storage.** `chart_drawings` is per user and symbol, so every timeframe shows the same drawings.
  - Versioned: an edit names its version, and a stale edit gets 409 with the current drawing. Undo/redo stay
    client-side.
  - Lockable (423 while locked) and soft-deleted. At most 500 drawings per symbol.
  - Migration `d7f9b1c3e5a7`, checked on Postgres.
- **Export / import.** `atp-drawings/1` JSON. An import is all or nothing, and the round trip is lossless.
- **Nothing here touches an order.**
- **Tests.** `tests/test_ch1_chart_drawings.py` (3). A mutation check confirmed it: removing the version check fails
  the tests.
- `app/charts` is added to the mypy gate.
- **Next.** CH1b: `frontend/src/charting/engine.ts` (`ChartEngine`), ProChart behind it with no visible change, and the
  drawings API client.

## CH1b (built): the ChartEngine interface, with ProChart behind it
- **Interface.** `frontend/src/charting/engine.ts` holds `ChartEngine`:
  - symbol, timeframe, bars and live bar;
  - studies;
  - drawings, plus `supports(kind)`;
  - layers with an `asOf`;
  - `serialize`/`deserialize`;
  - events: crosshair, click, `drawingChanged`, visible range.
- **Drawings client.** `frontend/src/charting/drawings.ts` has the `drawing/1` types and the backend's anchor rules,
  checked before a request. `drawingsApi` covers list, create, update with a version, lock, delete with a version,
  export and import. A stale edit raises `DrawingConflict` carrying the current drawing.
- **B-lite adapter.** `frontend/src/charting/lightweight.ts` puts `LightweightEngine` over ProChart's lightweight-charts
  v4 chart.
  - Drawn now: hline as a price line; trendline, ray and measure as a two-point line.
  - Other kinds are kept in the layout and drawn by the CH2 primitives core.
  - Layer lines become price lines.
  - `dispose()` removes every object the adapter added and unsubscribes its handlers.
- **ProChart.** It builds the engine over its own main chart and hands it out through `onEngine`. Nothing ProChart
  drew before changes.
- **Tests.** `src/charting/charting.test.ts` (7) covers:
  - schema parity with the backend;
  - 409 → `DrawingConflict`;
  - delete with a version;
  - render, keep and round-trip;
  - no leaked chart objects;
  - events carry data values.
- The frontend has 59 tests in total, and `tsc` is clean.

## Open questions (provisional answers, work continues)
- **CH-1. Order against parts H, S, B.** As above.
- **CH-2. TradingView Advanced Charts access** is a business application by Abhi (company and product details; the
  logo is required; licensed for company web apps). Track B goes ahead regardless; CH7 waits for access.
- **CH-3. `deepentropy/lightweight-charts-drawing`** (68 tools, v5, 0.1.x) is not vendored until its licence is
  confirmed and it passes a review. Until then we implement our own core set (CH2). KLineChart (Apache-2.0) is the
  fallback engine if primitives turn out too slow to build; the decision point is the end of CH2.
- **CH-4. lightweight-charts licence.** v5 is Apache-2.0 and requires the TradingView attribution link on the chart
  (`attributionLogo`); it stays on.
- **CH-5. Drawings scope.** Drawings are per user and symbol and shared across timeframes (time/price anchors).
  Sharing with team members is later (tenant-scoped, read-only).
