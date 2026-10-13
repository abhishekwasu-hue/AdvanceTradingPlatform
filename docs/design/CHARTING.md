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
- **Review follow-up (fresh-eyes pass).**
  - **A drag released before its save returns stays where it was dropped.** It is shown at once, and a second drag
    builds on it. Before, the second drag started from the old shape and undid the first on the server.
  - **Delete and lock read the drawing when their queued turn comes,** not when the key is pressed. A delete right
    after a move removes the moved drawing, and undo brings that one back. Two quick lock toggles unlock.
  - **404 (deleted elsewhere) now counts as a conflict.** Before, it was a generic error, which left a ghost drawing
    and an undo that failed forever. The client maps 404 to `DrawingConflict(null)` for update, delete and lock.
  - **After a conflict, a drag queued behind the failed one is not saved.** It was built on a version that another
    tab replaced.
  - **"Nothing changed" compares by meaning.** Times are compared as instants, because the server writes `…:00Z`.
    A drag that wanders back to its start saves nothing.
  - **Reload goes through the edit queue.**
  - **The test fake now answers like the server:** normalised times, and a missing drawing as a 404 conflict.
    5 new tests, and 5 mutations of the fixes are all killed.
  - **Known, not changed.** A lock set in another tab is not refreshed until reload. A local edit then gets the
    server's 423 message.
- **Next (CH2c-2).**
  - Pointer events on `LightweightEngine`: down, move and up converted with `coordinateToTime` and
    `coordinateToPrice`, and chart scrolling paused while dragging.
  - A toolbar in ProChart with the kinds, delete, lock, undo/redo, and Escape.
  - A text prompt for text drawings.
  - A headless check.

## CH2c-2 (built): the drawing tools on the chart
- **Engine** (`lightweight.ts`).
  - `toData(x, y)`: a pane point becomes a time and price, through `coordinateToLogical` and `logicalToTime`, plus
    `coordinateToPrice`.
  - `paneConverters()` gives the same mapping the primitives paint with, so a hit-test matches the picture.
  - `paneOffsetX()` is the width of the left price scale.
  - `candleTimes()`.
  - `setPanning(on)` sets `handleScroll` / `handleScale`.
- **Binding** (`toolsBinding.ts`, `bindTools(engine, controller, element, window)`).
  - With a tool active, each press places the next anchor. The press is stopped in the capture phase, so the chart does
    not pan, and the preview follows the pointer.
  - With no tool:
    - a press on a drawing selects it and starts a drag (a handle moves its anchor, the body moves the whole drawing);
    - panning is off and the pointer is captured until release;
    - a press on empty chart clears the selection and pans as usual;
    - a right press is ignored.
  - Keys:
    - Escape cancels;
    - Delete or Backspace deletes the selection;
    - Ctrl/Cmd+Z undoes;
    - Ctrl/Cmd+Shift+Z or Ctrl+Y redoes;
    - nothing fires while typing in a text box.
  - The selected drawing is shown 1 px bolder. That look is never saved.
  - `unbind` removes every listener and every drawing it drew, and turns panning back on.
- **ProChart.**
  - `DrawingToolbar` offers the 12 kinds, plus undo, redo, lock and delete.
  - A text label asks for its text with a prompt.
  - It shows the hint "Click on the chart to place it · Esc to cancel" and the controller's message.
  - The tools are on for full charts of a symbol (`drawingTools`, default true) and off in mini mode.
  - Drawings load per user and symbol through the CH1 API. A load error is shown on the toolbar.
- **Tests.**
  - `toolsBinding.test.ts` (6):
    - placing through the element and left-scale offset;
    - the press is stopped;
    - the preview;
    - the drag with panning off and back on, and pointer capture;
    - an empty press and a right press;
    - keys, including typing;
    - unbind;
    - the selected look is not saved.
  - Engine fakes extended.
  - 95 frontend tests pass, `tsc` is clean, and the build and bundle budget pass (the shell is unchanged at 81.6 KB).
  - 8 mutation checks, all killed:
    - offset ignored;
    - panning never restored;
    - the press reaches the chart;
    - typing triggers shortcuts;
    - stale drawings kept;
    - no selected look;
    - a right press acts;
    - unbind leaves drawings.
- **Headless check.** ProChart over synthetic bars, with the API answered in memory, in Chromium:
  - "Line" + 2 clicks gives one POST;
  - "Box" + 2 clicks gives one POST;
  - dragging the line's handle gives one PUT;
  - Ctrl+Z gives one PUT back to the original anchor;
  - there are no page errors;
  - both drawings render where they were clicked.

## Z7a-3 (built): the market-zones layer (CH3 / U8 minimal style)

- Backend: `GET /api/charts/layers/market-zones?instrument=&at=&price=&family=&max_bands=` (flag `market_zones`, off ->
  503): the organisation's stored zones as of `at` (timezone-aware; never a later snapshot, never another
  organisation's), at most 6 bands, every number and text included (bounds, label prices, touches, strength, distance).
  An organisation whose own zone settings have no snapshot yet gets the platform's as a labelled fallback
  (`fallback: true`; every band label ends "· default settings"); ScreenQL never falls back.
- One rule, two places: `app/price_action/zones/chart.py` `select_bands` (also for the U8 evidence charts drawn
  server-side, e.g. Telegram images) and its port `frontend/src/charting/marketZones.ts` `selectBands` (re-picks when the
  price on screen moves); both are tested against `backend/tests/fixtures/zones/chart_bands_cases.json`.
- Pick: role by price (demand at or below, supply above); per role the strongest of each overlapping cluster; the
  nearest 3 per role, a short side's slots to the other; nothing beyond 10 %; top to bottom. Labels
  "demand 512.30 (fresh)" / "supply 530.00 (tested)" + "2 touches".
- Engine: `LayerData.bands` (generic `LayerBand`: low, high, colour, opacity, line style, label) drawn by the
  lightweight engine as one `LayerPrimitive` under the drawings (`geometry.bandShapes`: a tinted band, a line when
  low == high, the label at the right edge). `MarketZonesLayer` fetches, draws and shows a one-line legend; mounted on
  the full-window chart. Open questions Z7a3-1, Z7a3-2, Z7a3-8 (`docs/OPEN_QUESTIONS.md`).

## CH3a (built): the price-action layer and replay

- **Backend.** `GET /api/charts/layers/price-action?instrument=&tf=&at=&start=&degree=&debug=` (`app/charts/price_action_layer.py`;
  flag `chart_price_action`, off -> 503, per organisation; classified "member" in the tenancy table).
  - Reads the shared lake's bars ending in (`start`, `at`] - the timeframe's own bars, else rebuilt from 1-minute bars
    with the market-zones job's conversions - so a bar after `at` (or still forming at `at`) is never read.
  - Computes, in a worker thread, with the existing engine: causal swings per degree (`causal_swings`; a swing exists
    from the bar that confirmed it), legs, real breaks of the newest swings (`breaks.first_real_break`), live swing
    levels (nearest 3 above and below the last close) and the Dow trend per degree (HH + HL up, LH + LL down).
  - Default view: degree 2 only ("significant"). Debug: every degree, the tentative swing, broken levels, thresholds.
  - Every number has its text; `fingerprint` (SHA-256 of the canonical content) - the same bars give the same bytes.
  - Window: default days per timeframe, at most 4x and 5,000 bars, refused rather than cut. A future `at` reads as now.
- **Frontend.** `charting/priceAction.ts` turns the response into `LayerData` by the toggles (swings, legs, breaks,
  levels, trend). `LayerData` gains `segments` and `labels`, painted with the bands in the one `LayerPrimitive`
  (`geometry.layerShapes`); levels are price lines. `PriceActionLayer.tsx`: the panel with the toggles (kept per browser),
  Debug, and the trend per degree (top first). Mounted on the full-window chart (`ChartWindow`).
- **Replay** (`charting/replay.ts`, `ReplayBar.tsx`): start 100 bars back, step back / forward, play (one bar a second),
  exit. The chart shows candles up to the replay bar only; the price-action layer and the market zones are asked for as
  of that bar's end; the window start is fixed at the first step so each step only adds; the live price is off.
- **Tests.** `backend/tests/test_ch3_price_action_layer.py` (8): golden (byte-identical, and the frontend copy), no
  look-ahead through the API (later bars added, later bars re-versioned, another source - the layer at `at` is
  unchanged) and on the pure function at every 7th bar, replay only adds and shows nothing later than its bar, native
  15m == 15m rebuilt from 1m (and a mid-bar `at` ignores the forming bar), flag per organisation, input checks.
  A mutation that reads bars up to two hours past `at` fails two of them. Frontend: `priceAction.test.ts` (10) on the
  same golden (the replay as-of time equals the backend's `as_of`), plus the engine test for segments and labels.
- **Review follow-up (#212).**
  - Look-ahead test of the engine itself: the layer over bars[:n] equals the layer over all bars filtered to what was
    confirmed by bar n's end (swings, legs, breaks), at every 7th n. A ZigZag reading the next bar's threshold fails it.
  - No stale layer in replay: every new request clears the layer first (`LayerLoader`); a late answer to an older step
    is dropped; a failure leaves no layer and says why; the replay never sends `start >= at` (`windowStart`); the live
    layer is asked again when a new candle starts; a candle time without an offset is read as IST.
  - Reads are bounded: 1-minute rebuilds at most 180 days, every read row-capped (422, never cut), a 30 s cache per
    (instrument, timeframe, window, as-of minute) and 120 requests a minute per user.
  - Provenance: shared-licensed sources only (`nse_bhav:`, `vendor:`, `r2:`; never `stream:` / `broker:`), source
    preference before version; corrections point-in-time (CH3-3, CH3-6). `at` / `start` before 1990 -> 422.
- Open questions CH3-1 .. CH3-6 (`docs/OPEN_QUESTIONS.md`); CH3-4 lists what is left for later.

## CH3b (built): impulse vs corrective legs, BOS / CHoCH, liquidity, trendlines, the top-down panel

Ported from Trade@1e6c9ac with golden parity (`backend/tests/ch3b_golden.py`, `test_ch3b_trade_parity.py`: Trade's
own output on the frozen fixtures, checked against Trade live when the repository is on disk), never rewritten:
- `app/price_action/legs.py` (Trade `price_action/legs.py`): strong / weak impulse, healthy / mixed / dangerous
  pullback, reversal, range, from efficiency, overlap, displacement / FVG, depth, speed, CHoCH. The layer classifies the
  legs between its own degree swings (internal swings one degree lower).
- `app/price_action/structure_tracker.py` (Trade `opportunity_engine/structure.py`): the bar-by-bar structure state
  machine - BOS, CHoCH, reversal, recovery, range, sweeps, the protected level.
- `app/price_action/liquidity.py` (Trade `decision3/liquidity.py`: pools, sweeps, marks; not the trapped zones): pools
  at swing highs / lows, equal highs / lows, PDH / PDL, PWH / PWL; a sweep is a wick or a short stay beyond and back.
- `app/price_action/trendlines.py` (Trade `price_action/trendlines.py` + `find_trendline` + `ref_range`): the newest
  falling resistance and rising support; the channel is the parallel through the farthest opposite extreme (ATP).
- `app/charts/price_action_concepts.py` puts them on the layer (schema `pa-layer/2`): `legs[].class`, `structure`,
  `structure_state`, `liquidity`, `sweeps`, `trendlines`, and `mtf` - the structure state on W / D / 1H / 15m, each
  from its own lake bars as of the same `at` (weekly from daily when the lake has none). Same flag, same provenance.
- Frontend: toggles BOS/CHoCH, Liquidity, Trendlines (Breaks now off by default); impulses drawn bolder with their class;
  events as labels with a dotted run from the broken swing; the protected level, pools (BSL / SSL) as lines; sweeps
  marked; trendlines with a dashed channel; the top-down row in the panel.
- No look-ahead: the truncation-invariance test now also covers leg classes and structure events; the API test alters
  and adds later bars on every top-down timeframe and the whole answer at `at` stays byte-identical; replay only adds
  structure events and a confirmed leg keeps its class. Open questions CH3-4, CH3-7, CH3-8.
- **Review follow-up (#218).** Each top-down row is cached by (instrument, timeframe, window start on a fixed grid, the
  lake label and a probe of its rows: newest END, count, highest version), so a replay step recomputes only the rows
  whose bars changed (tested: 8 x 15m steps compute D and W once); at most 2 layer / row computations at once per
  process; the layer cache is bounded by bytes and never holds debug answers; 60 requests a minute per user; a
  top-down row over 5,000 bars is refused, not cut; a daily chart reads its daily bars once for the chart and D / W.

## CH4a (built): the trades layer - your own PAPER trades on the chart

The first CH4 slice that the data ATP has supports (dependency check below; OPEN_QUESTIONS CH4-1 .. CH4-3). Flag
`chart_trades_layer`, off by default.

- **Backend.** `GET /api/charts/layers/trades?instrument=&at=&start=` (`app/charts/trades_layer.py`).
  - Off -> 503, per organisation. Tenancy: "member", but the rows are the caller's own (`user_id` = the caller and
    `tenant_id` = the caller's organisation), never a teammate's, never another organisation's. The trade list itself
    is organisation-wide; a chart layer is personal.
  - PAPER rows only.
  - The instrument's own trades are drawn at their prices; a derived contract (an option or future on this underlying)
    at its times only (`on_price_axis: false`). Its premium never goes on the underlying's axis.
  - As of `at`: no trade entered after it, no exit after it (the trade is open then, without P&L), so replay shows
    nothing before it happened.
  - The window is the trades active in (`start`, `at`], at most 200. Read-only: it writes nothing and offers no
    action.
- **Frontend.** `charting/tradesLayer.ts` (API, bar snapping, `toTradesLayer`, a loader that clears on every new
  question) and `TradesLayer.tsx`, a read-only panel ("My trades": count, open count, show / hide). They are mounted on
  the full-window chart, following replay.
  - Each entry and exit is a label in the bar it falls in.
  - A closed trade on its own chart has a dashed entry-to-exit segment.
  - An open trade on its own chart has its stop and target as price lines.
  - Colours are Okabe-Ito, by side.
- **Tests.** `tests/test_ch4a_trades_layer.py` covers:
  - own trades only (a teammate's and another organisation's are never drawn, a LIVE row never);
  - instrument, exchange and contract matching;
  - as-of (no trade and no exit before it happened) and the window;
  - flag off / per organisation;
  - writes nothing.

  Frontend `tradesLayer.test.tsx` covers snapping, shapes, the contract case, the loader and the read-only panel.
- **Dependencies of the rest of CH4, checked on main 53c54ee:**
  - **Part B lake.** The shared lake has daily F&O contract bars with OI from the F&O bhavcopy (HB1,
    `nse_bhav:fo`, keys `NFO:<SYM>:FUT:<expiry>` and `NFO:<SYM>:<CE|PE>:<expiry>:<strike>`), once the backfill has
    run. There is no IV history and no intraday chain history: the `md_option_chain_snapshots` table exists but
    nothing writes it, and `option_chain_snapshots` (Phase W) holds only near-the-money samples of running option
    deployments plus uploads, trimmed by retention.
  - **U1-c.** Lots (`instrument_master_versions`, `instruments.lot_size`) and the exchange ban list
    (`fo_ban_history`) exist (`app/universe/fo_eod.py`).
  - **S3 Notification Service.** It exists (`app/notifications/service.notify` with the alert dispatcher).
- **Next (CH4b, data-ready):**
  - option and future contract charts and synthetic spreads from the lake's daily `NFO:` bars (sum of legs with sign
    and quantity);
  - an OI pane from the same rows;
  - option symbol search from `instruments`;
  - chain -> chart linking.

  Intraday contract charts, IV / Greeks panes and IV / PCR / max-pain history wait for an intraday chain store
  (CH4-1).

## CH4b (built): daily contract and spread charts, OI, contract search, chain -> chart links

Flag `chart_contracts`, off by default; built on CH4a's branch. The daily data the shared lake already holds:
CH4-1's data-ready slice.

- **Backend** (`app/charts/contracts.py`; tenancy "member", platform reference data):
  - **`GET /api/charts/contracts/series?leg=[qty*]KEY&...&at=&start=&iv=`**
    - Daily F&O bars from the shared lake (HB1 `nse_bhav:fo` keys; shared-licensed sources only, the preferred
      source then the highest version per day, corrections point-in-time), as of `at`. Expired contracts included.
    - One leg: its OHLC, volume and OI.
    - Up to 4 legs: the spread close = sum of close x qty on the days every leg traded (no invented OHLC), with each
      leg's OI.
    - `iv=true`: a COMPUTED daily IV per option leg (Black-76 from the close and the same-expiry future's close,
      `RISK_FREE_RATE`), labelled in `iv_note`; null where unsolvable or with no future that day.
  - **`GET /api/charts/contracts/search?underlying=&kind=&expiry=&near=`** lists the instrument master's contracts
    with their lake keys, one row per contract across brokers, nearest expiry then nearest strike first.
- **Frontend:**
  - `charting/contracts.ts`: API, keys (the bhavcopy's strike text), URLs, candles, chain links.
  - `pages/ContractChartWindow.tsx`: `/?contract=<leg>&contract=<leg>`, its own tab. The ProChart daily candles (a
    spread as its close line), each leg's OI bars and the computed IV, labelled.
  - Option chain: "chart" links on each strike's call and put. They appear only for a live (broker) chain with the
    flag on, never for a sample chain.
  - No order control anywhere.
- **Tests:** `tests/test_ch4b_contracts.py` covers:
  - keys equal the bhavcopy's;
  - one leg's OHLC / OI from shared sources only (no `stream:` row);
  - a correction counts only once ingested by `at`;
  - spread = sum of legs on common days;
  - computed IV = the Greeks library's Black-76 solve;
  - search de-duplication and order;
  - refusals and the flag.

  Frontend `contracts.test.tsx` covers keys, URLs, chain links (not for a sample chain), candles, the IV label and
  the OI figure.
- **Not yet:** intraday contract charts, quoted IV / Greeks panes, PCR / max-pain / OI-by-strike history (CH4-1:
  no chain history store), the basis / participant / rollover charts (CH4-4), and the options layer on the
  underlying.

## CH4c (built): futures basis, cost of carry, calendar spread and rollover

Flag `chart_futures_basis`, off by default, on top of `chart_contracts`. Built on CH4b's branch. It uses the daily data
the shared lake already holds: the CH4-4 charts that are data-ready.

- **Backend.** `GET /api/charts/contracts/basis?underlying=&at=&start=` (`app/charts/futures_basis.py`; tenancy
  "member", platform reference data). Per trade date, as of `at`:
  - near, next and far months by expiry, each with its close and OI. A contract is live on its expiry day; the next day
    the near month rolls, and the response lists the roll days.
  - The spot: the cash close, or the index close for an index future.
  - basis, basis %, annualised carry, calendar spread (next - near), all-expiry OI and rollover %.
  - Reads come from shared-licensed sources only. Per contract and trade date the preferred source wins, then its
    highest version; a correction counts once ingested by `at`.
  - The futures keys are every possible monthly expiry date in the window, read by exact key in chunks of 5,000
    (under Postgres's bind-parameter limit).
- **Frontend.**
  - `charting/futuresBasis.ts`: the API, the URL and the line geometry.
  - `pages/FuturesBasisWindow.tsx`: `/?basis=<SYM>`, its own tab. It draws four small lines (basis %, carry %,
    calendar spread, rollover %) with the roll days dashed, and the last day's reading in words.
  - A single futures contract's chart links to the page, only when the page is on.
  - There is no order control anywhere.
- **Tests.** `tests/test_ch4c_futures_basis.py` checks:
  - near / next / far and the roll after the expiry day;
  - the figures;
  - as of `at`;
  - that broker and stream rows are never read, and the bhavcopy wins per trade date;
  - the index spot;
  - chunked reads;
  - flags and refusals.

  `futuresBasis.test.tsx` checks the URL, the last reading, the lines, the panel's wording and the link.
- **Not yet:** the participant-wise OI chart (the NSE participant file is not ingested), intraday basis, and the
  options layer on the underlying (CH4-1, CH4-5).

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
