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

## CH2a (built): lightweight-charts 4 -> 5

- `lightweight-charts` goes from ^4.2.3 to ^5.2.1 (lockfile updated by npm, not by hand).
- **API moves.**
  - `addCandlestickSeries` / `addLineSeries` / `addHistogramSeries` become `addSeries(CandlestickSeries | LineSeries |
    HistogramSeries, ...)`.
  - Markers are now a series plugin: `createSeriesMarkers(series)`, created once per series and dropped on teardown.
  - The `ChartLike` slice in `charting/lightweight.ts` follows (`addSeries(LineSeries, ...)`).
- **Behaviour.** No intended visual change: same series, options, price lines, markers, sync and resize.
  - Checked with tsc, vitest, build and the bundle budget.
  - A headless render of ProChart (candles, EMA lines, volume pane, markers, a price line, an `hline` and a
    `trendline` drawing through the engine) on v4 and on v5 differs in 11 of 1,152,000 pixels, at the price-axis
    label edge.
- **Size.** The lazily loaded `charts` chunk grows from 51.8 to 58.8 KB gzip (v5 ships the plugin and primitive
  APIs that CH2b uses). Initial JS is unchanged at 81.6 KB of the 300 KB budget.
- **Next (CH2b).** The primitives drawing core on v5 (`ISeriesPrimitive`): parallel channel, rectangle/zone, Fibonacci,
  text, long/short position, vertical line. Then the kinds `supports()` reports false today are drawn.

## CH2b (built): the primitives drawing core on v5

- **Every `drawing/1` kind is drawn now.** `supports()` is true for all 12 kinds.
  - `hline` stays a price line, so it keeps its axis label.
  - Every other kind is one v5 series primitive (`ISeriesPrimitive`) attached to the candle series.
- **Pure geometry.** `frontend/src/charting/geometry.ts` turns a drawing plus converters (time to x, price to y, pane
  size) into plain shapes: segments, rectangles, polygons and text. It does not touch the chart library or a canvas.
  - Rectangle/zone: a filled box between the two anchors.
  - Vertical line: spans the pane at its time.
  - Parallel channel: the third anchor sets the offset of a line parallel to the first two; fill plus dashed midline.
  - Fibonacci retracement: 0 at the second anchor, 1 at the first. Default levels are 0, 0.236, 0.382, 0.5, 0.618,
    0.786 and 1; the drawing's own `levels` replace them. Each label shows the level and its price.
  - Fibonacci extension: the first move (anchor 1 to 2), projected from the third anchor. Default levels are 0,
    0.618, 1, 1.618 and 2.618.
  - Ray: extends to the right edge. Trendline and channel extend only per `style.extend`.
  - Measure: labels the change and the percentage.
  - Text: drawn at its anchor.
  - Long/short position (entry, stop, target): profit and loss zones, target and stop labels with percentages, and
    R:R. A stop or target on the wrong side of the entry is shown as a problem, never silently flipped.
- **Labels come from anchor prices, never from pixels.**
- **Time to x.** An anchor time becomes a fractional bar index over the candles' real times, then x through
  `logicalToCoordinate`.
  - Between bars it interpolates, so a drawing made on 5m lands correctly on 15m.
  - Past either end it extrapolates with the nearest bar spacing, so a future target still has a place.
  - An overnight gap is one bar step, not hours of empty space.
  - Bar times are re-read only when the candle data changes (`subscribeDataChanged`), not on every paint.
- **Painter.** `frontend/src/charting/primitives.ts`: one `DrawingPrimitive` per drawing. It paints in media
  coordinates on the top layer and always restores the canvas state. `dispose()` and `removeDrawing` detach it.
- **Tests.**
  - `geometry.test.ts` (11): every kind's geometry, the time mapping, R:R and its wrong-side checks, "nothing drawn
    when an anchor cannot be placed", and the painter.
  - `charting.test.ts` (8): primitives attached and detached, converter caching, no leaked objects.
  - Mutation checks failed the suite as they should: interpolation, channel offset, short risk, Fibonacci direction,
    ray extension, cache invalidation, the hline guard, the polygon guard and canvas restore.
- **Headless render.** ProChart with all 10 drawn kinds over synthetic bars shows each one in its place, with no
  page errors from the chart.
- **Next (CH2c).** Interactive drawing tools on the chart (click to place anchors, drag handles, select, delete),
  stored through `drawingsApi`, with undo/redo over versions and lock.

## CH2c-1 (built): the drawing tools without a chart
`frontend/src/charting/tools.ts`. A pointer on the chart becomes a data point (time in epoch seconds, price) before it
reaches this file, so every tool is a plain function or one controller, tested without a browser. CH2c-2 wires the
chart's pointer events and a toolbar to it.

- **Placing.**
  - One click per anchor, and each anchor keeps its rule: a stop or target is price-only, a vline is time-only.
  - While placing, the pointer stands in for every missing anchor, so the drawing follows it.
  - The last click saves the drawing and selects it.
  - A drawing the backend would refuse is not sent (`drawingProblem`), and the message is shown.
  - Escape drops a half-placed drawing.
- **Hit-testing.**
  - The drawing under a pane point, within 6 px.
  - Handles win over bodies, and the drawing painted last wins.
  - Distances are geometric: to a segment, inside or to a box or polygon, and a box around a label.
  - Handle positions:
    - a price-only anchor sits at the time of the drawing's first timed anchor, else mid-pane;
    - a time-only anchor sits mid-height.
- **Dragging.**
  - A handle moves one anchor, keeping its rule.
  - The body moves in **bars** (`logicalToTime`, the inverse of `timeToLogical`), not seconds, so a drag across a
    night or a weekend keeps the shape.
  - The drag is shown live and saved once at the end, with its version. A move that changed nothing saves nothing.
- **Lock.** A locked drawing does not move, delete, or undo/redo, and the message says to unlock it first. The server
  refuses too (423).
- **Undo / redo.**
  - Client-side over the saves (create, move, delete), capped at 100 steps. A new edit clears redo.
  - Undoing a delete re-creates the drawing under a new id, and the later steps follow the new id.
- **Conflicts.**
  - A stale edit (another tab) shows the latest version, or drops a drawing deleted elsewhere.
  - It also forgets that drawing's undo steps, so an undo never overwrites someone else's change.
  - Any other error keeps the drawing as it was and shows the message.
- **One edit at a time.** Edits run in order, so a double click never saves twice in parallel against the same
  version.
- **Tests.** `tools.test.ts` (12).
  - A real bug was caught on the first run: a failed create showed no message.
  - 9 mutation checks, all killed:
    - the body drag in seconds;
    - a conflict keeps the undo steps;
    - no re-key after a re-create;
    - a new edit keeps redo;
    - edits in parallel;
    - a locked drawing moves;
    - a no-op drag saves;
    - handles ignored;
    - history uncapped.
- **Next (CH2c-2).**
  - Pointer events on `LightweightEngine`: down, move and up converted with `coordinateToTime` and
    `coordinateToPrice`, and chart scrolling paused while dragging.
  - A toolbar in ProChart with the kinds, delete, lock, undo/redo, and Escape.
  - A text prompt for text drawings.
  - A headless check.

## Open questions (provisional answers, work continues)
- **CH-7. What undo covers.** Provisional:
  - Undo/redo cover create, move and delete, in this tab, until the page reloads.
  - Lock and unlock are not undo steps.
  - A conflict from another tab drops that drawing's steps.
  - Owner question: should undo history survive a reload (stored per user)? Should lock be undoable?
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
- **CH-6. Position tool width.** `drawing/1` has no end time for long/short positions, so the box runs from the
  entry to the right edge. Provisional: keep it so. If a fixed width is wanted, an optional fourth anchor (time
  only) can be added in a schema minor version, and old drawings keep working.
