# Chart interaction and purposeful colour: the independent review (23 Oct)

This review checks the order `ATP_SESSION_CHART_AND_COLOUR.md` against main `12d7bd8b` (#415). A separate agent did it
by reading the frontend code (and the lightweight-charts 5.2.1 development build for the gestures). It changes nothing
in the repository. Where it differs from the order, the finding wins until the owner says otherwise. The questions are
CC-1..CC-10 below, each with a provisional default, for `docs/OPEN_QUESTIONS.md`.

**Migrations: none.** Two things need a backend schema change, but no Alembic revision:
- linking the scroll in Multi-chart adds a field to `GroupSync` (`backend/app/charts/layouts.py:60-64`, `extra="forbid"`);
- a Log / % / Invert setting stored on the server adds a field to `ChartPrefs` (`backend/app/me/prefs.py:77-83`, `extra="forbid", strict=True`).

Both are stored as JSON, so new fields with defaults are enough. Everything else is frontend only.

## "FOUND IN THE CODE": confirmed or corrected

| Claim | Verdict | Evidence |
|---|---|---|
| lightweight-charts 5.2.x | Confirmed: 5.2.1 | `frontend/package.json:32` (`^5.2.1`); `package-lock.json:5111-5113` |
| Options live in `chartCore.ts` and are duplicated in `ProChart.tsx` | Confirmed. The copy is word for word. | `src/chart/chartCore.ts:137-139`; `src/components/ProChart.tsx:96-100` (`baseOptions`) |
| `vertTouchDrag = false` | Confirmed in both | the lines above. `CandleChart.tsx:69-80` sets no handleScroll, so the library default (`vertTouchDrag: true`) applies there. CandleChart is never rendered (see below). |
| The price scale is always auto | Confirmed. Nothing sets `autoScale`, `setAutoScale`, `PriceScaleMode` or `trackingMode`. | grep over `src/` finds nothing. `chartCore.ts:145-150` widens the auto range so it includes the level lines. |
| Reset only through `axisDoubleClickReset` | Partly. The double-click (and double-tap) reset is on in both. ProChart also has a "Fit all candles" button. ChartV2 / ChartsPageV2 has no reset control at all. | `chartCore.ts:138`; `ProChart.tsx:99`, `:521` (`fit()`, Scan icon) |
| Three chart implementations | Corrected: two are live. `CandleChart.tsx` is **never rendered**. Only its types (`ChartMarker`, `PriceLineSpec`, `directionMarker`) are imported. | `ProChart.tsx:13,25,28`; `components/ChartStrategies.tsx:6`. No `<CandleChart` anywhere in `src/`. |
| The page list | Corrected (see the next table) | |
| ProChart loads older bars near the left edge | Confirmed | `ProChart.tsx:364-368` |
| Does chartCore load older bars too? | **Yes, already.** `onLoadOlder` within 10 bars of the oldest bar, and `setData` keeps the view when older bars are put in front (same key). All three v2 hosts pass `data.loadOlder`. | `chartCore.ts:50-51,161-163,269-279`; `ChartsPageV2.tsx:301,309`; `MultiChartV2.tsx:332`; `EmbeddedChartV2.tsx:86`; test `ChartV2.test.tsx:119,142`; e2e `charts-v2.e2e.ts:177` |
| Tokens in `tokens.css` | Confirmed: brand / up / down / warn, plus info, ai, ai-2, signal, armed, the surfaces and the chart tokens | `src/styles/tokens.css:6-68,137-217` |
| Enforced by `check-v2-colours.mjs` | Confirmed, but only for the files in scope: `src/ui`, `src/chart`, `src/broker`, `src/tours`, `src/aiKeys`, `*V2.tsx`. `components/ProChart.tsx` (hex `COLORS`, `:51-55`) and `charting/priceAction.ts:109-110` (hex) are **not** checked. Today: 158 files, 0 findings. | `scripts/check-v2-colours.mjs:10-16,65-74` |
| Palette generator `gen-palette.mjs` | Corrected: it writes only the **legacy** Tailwind-hue remap `palette.css` (light mirror + colour-blind swap) for the old pages. The semantic tokens are written by hand in `tokens.css`, and their contrast is pinned by `src/ui/tokens.test.ts`. | `scripts/gen-palette.mjs:1-4,21-37`; `ui/tokens.test.ts:1-60` |

### Which chart every page really uses (ui_v2 is ON in production)

| Page / component | Chart | Seen by a ui_v2 user? |
|---|---|---|
| Charts page `/charts` (Chart tab) | `ChartV2` → `ChartCore` | yes (`shell/pagesV2.ts:20`, `ChartsPageV2.tsx:24`) |
| Charts page, Multi-chart tab | `MultiChartV2` → `ChartV2` | yes, while `chart_workspace` is on |
| Signals detail (`SignalPartsV2`) | `EmbeddedChartV2` → `ChartV2` | yes (`SignalPartsV2.tsx:118,133`) |
| **Test a strategy** (`BacktestPage`, `/strategies/test`) | **ProChart** with trade markers | **yes**. It has no v2 layout, so it renders inside the v2 shell (`routes.ts:21`, `BacktestPage.tsx:482`). **The order does not list it.** |
| `ChartWindow` (`?chart=`) | ProChart (+ market zones, PA layer, studies, replay) | no: it redirects to `/charts` under v2 (`ChartWindow.tsx:32-47`) |
| `ChartWorkspace` (`?workspace=1`) | ProChart | no: it redirects to the Multi-chart tab (`ChartWorkspace.tsx:57`) |
| `ContractChartWindow` (`?contract=`) | ProChart | only through an old link. Only the legacy `OptionChainPage.tsx:171` builds that link. |
| `PositionChartCard` | ProChart | no: legacy `PositionsPage` only. `PositionsV2.tsx:433` links to `/charts`. |
| `MarketPulseCard` | ProChart (compact) | no: legacy `DashboardPage` only |
| `SignalsPage` | ProChart | no: legacy. Pinned by `chartConsistency.test.tsx:159-176`. |
| `ChartStrategies` | not a chart: the ProChart strategies panel / hook (`ProChart.tsx:183`) | via ProChart only |

## Blocking findings and their resolution

| # | Finding | Resolution |
|---|---|---|
| R1 | The premise of Part 1 does not match the code. CandleChart is dead code. Under ui_v2, ProChart is reached only from "Test a strategy" (plus old `?contract=` links). Every other ProChart page is a ui_v2-off legacy page. Making CandleChart and ProChart "delegate to the core" means rewriting a 635-line legacy component whose pages are going away. That goes against "keep what is built" and "new user-facing work behind `ui_v2`". | Part 1 becomes: (a) one option builder `chart/chartOptions.ts` (theme-neutral: scroll / scale / kinetic / tracking / axis), imported by ChartCore and ProChart's `baseOptions`. This removes the duplicate with zero visual change. (b) Move the CandleChart types and `directionMarker` to `charting/markers.ts` and delete the component. (c) Give ChartCore what BacktestPage needs (series markers), then switch BacktestPage to ChartV2 under ui_v2. ProChart stays for ui_v2 off. (d) ProChart is frozen: the Part 2 gestures are core-only. ProChart is removed when the legacy shell is retired. |
| R2 | "No feature lost" has no inventory. ChartV2 lacks many ProChart features (feature table below): the crosshair OHLC / volume readout, series markers, S/R zones, Bollinger / Supertrend / ADX, hollow / area / Renko / range, the strategies panel, the market-zones and PA layers, the studies panel, replay, session breaks, "No older history", Fit. Some of these are already missing on the v2 Charts page today (the PA layer, zones, studies and replay are mounted only in `ChartWindow.tsx:7-10`, which v2 redirects away). | Write the inventory into the plan (feature table below). Part 1 ports only what a v2 page needs now: markers, the readout (needed by 2.3 long-press anyway), Fit → Reset, "No older data". The PA layer, market zones, studies and replay come through the Indicators menu "Layers" group that UI v2 §6 already plans, one PR each, behind their existing flags. They are not in this order. |
| R3 | 2.1 "a vertical drag switches the price scale out of auto mode" is **not native**. In 5.2.1 a plot drag pans the price only when auto-scale is already off at the start of the drag (`PriceScale.startScroll/scrollTo` return early `if (isAutoScale())`), and the drag starts at the first pixel of movement (pane widget: `_startScrollingPos` → `startScrollPrice`). Only a drag on the price axis turns auto-scale off (`scaleTo` → `setMode({autoScale:false})`). The library has no event for an auto-scale change. | A small gesture layer in the core (`chart/gestures.ts`, React-free, unit-tested). A plot drag whose vertical part passes a threshold calls `priceScale.setAutoScale(false)` and pans the price itself with `IPriceScaleApi.getVisibleRange / setVisibleRange` (in 5.2). Time stays native. The "A" state is read from `priceScale().options().autoScale` on pointer-up / wheel / dblclick, and on the gesture layer's own changes. Percentage mode refuses price scroll in the library: the core turns "%" and manual price off together. |
| R4 | A latent gesture bug that the order would make worse. `LightweightEngine.setPanning(true)` (`charting/lightweight.ts:165-168`, called by `toolsBinding.ts:104,172` after a drawing drag) applies `handleScroll: true, handleScale: true`. The library expands a boolean into every sub-option (`migrateHandleScaleScrollOptions`), so after the first drawing drag `vertTouchDrag` is on: a phone swipe stops scrolling the page, and `axisPressedMouseMove` / `axisDoubleClickReset` lose their settings. With an "activated" state that toggles options, the two writers would fight. | One owner of the gesture options: the core keeps the current interaction state and applies the builder's options. `setPanning(enabled)` asks the core ("suspend / resume"); it never writes booleans. A unit test: after a drawing drag, `vertTouchDrag` is back to the state's value. Fix this in the Part 1 PR. |
| R5 | Part 3 reverses rules of the accepted UI v2 proposal §8 (`docs/design/UI_V2_PROPOSAL.md` "Colour"). §8 says: "A successful state ('Connected', 'Done') is neutral with a check icon, never green"; "Error chips are amber"; `--info`, `--ai`, `--signal`, `--armed` are "not used on v2 trader screens"; "`--ai` stays inside Copilot only". The order says: Connected = green, Error = red, Info = teal, violet for AI chips everywhere. Green for "Connected" also takes the colour that §8 keeps "**only**" for profit / rising / Long. In colour-blind mode it then turns profit-blue, close to the brand blue. | Owner decision CC-1, recorded in DECISIONS as superseding §8's colour rules (DECISIONS line: "the brand accent stays ATP blue; colour by meaning"). Provisional default: **status chips keep their words and icons, and colour follows meaning without reusing P&L colours.** Connected = neutral + check (as today). Needs action = warn. Error = down only when money or an order is at stake, otherwise warn. Info = info. AI = ai, on AI entry points only. Update `ui/tokens.test.ts` PAIRS for every new pair. |
| R6 | "Paper = orange" collides three ways. (1) `--armed` is already orange and is reserved for "auto-execution armed / LIVE" (`tokens.css:35-38`). (2) In colour-blind mode **loss turns orange** (`tokens.css:73-74`: dark 249 115 22, light 194 65 12), so an orange PAPER chip reads as a loss. (3) In the light theme, warn (180 83 9) and armed (176 56 8) are almost the same brown-orange. Today PAPER is a neutral outline chip and Live is the solid / warn chip (`dashboard/CardV2.tsx:72-79`, `shell/SidebarV2.tsx:103`, `paperbot/PaperBotV2.tsx:242`). In Phase 0 every row is PAPER, so an orange PAPER badge would sit on every screen as permanent colour noise, and the safe mode would look like the alarm. | CC-2, provisional: **PAPER stays the neutral outline chip with its word** ("Paper"). An optional small practice icon is fine, but no new hue. If Abhi still wants a hue for paper, it needs its own token that is distinct from warn, armed and the colour-blind down in all four theme variants, and a test proves it. Nothing in the current palette is free for that. |
| R7 | "Generated through `scripts/gen-palette.mjs`" is wrong (see the table above). The generator writes legacy hue variables. Adding semantic tokens there would put them into `palette.css`, which the v2 colour check is meant to keep out of v2 screens. | New tokens go into `tokens.css` (all four blocks: dark, light, colour-blind dark, colour-blind light, plus the chart-scoped blocks where a chart uses them), with `tailwind.config.js` names and `ui/tokens.test.ts` pairs. `gen-palette.mjs` is not touched. |
| R8 | "A deeper navy-slate dark theme" changes existing token values. `:root` is shared by both shells, so this also changes every ui_v2-off page and `--chart-bg` (which mirrors `--surface`). §8 says v2 "changes no existing token value". It also moves 222 of the 458 e2e baselines (`e2e/__screenshots__`). | CC-3, provisional: v2 surface values under a v2 root attribute that `AppShellV2` sets (e.g. `:root[data-shell="v2"]`), so ui_v2-off pages stay as they are. The chart's own dark block (`[data-chart-theme="dark"]`) moves only if Abhi approves it on the comparison page. The baselines are refreshed in their own PR. |
| R9 | 2.5 memory: a visible *logical* range is only meaningful for one bar array. Loading older bars, a new day's bars or another history window shifts it, so a restored range shows the wrong place. A range event fires on every pan frame, so saving on every event writes `localStorage` dozens of times a second. | Store `{barSpacing, rightOffset (timeScale().scrollPosition()), autoScale, mode}` per user + symbol + exchange + timeframe. Do not store the price range: prices move. Restore with `applyOptions({timeScale:{barSpacing}})` and `scrollToPosition`. Save debounced (≈500 ms after the last change), every access in try / catch, a capped list (≈50 keys, oldest dropped). "Reset" deletes the key. |

## Part 1: one core (notes)

**Feature inventory** (✓ has it, – lacks it). Tests that pin each feature are in brackets.

| Feature | ChartCore / ChartV2 (+ hosts) | ProChart | CandleChart |
|---|---|---|---|
| Panes | volume, RSI, CH6 study pane as v5 native panes [`ChartV2.test.tsx:29,84,99`] | volume, RSI, ADX as separate charts synced by hand (`:351-382`) | – |
| Overlays | EMA fast / slow, VWAP, SMA [`:109`] | + Bollinger, Supertrend; strategy defaults (`indicatorsForStrategy`) | – |
| Chart types | candles, bars, Heikin Ashi, line [`:59,73,130`] | + hollow, Renko, range, area (`chartTypes.chartView`, brick legend) | candles |
| Drawings | ✓ via `LightweightEngine` + DrawingRail / DrawingMenu, undo / redo [`drawingRail.test.tsx`, e2e `charts-v2:502-545`] | ✓ DrawingToolbar; phone Tools sheet | – |
| Layers | My trades (`useTradesLayer`) | ChartWindow adds market zones, PA layer, studies, replay | – |
| Level / price lines | `setLevels` dashed + labelled, kept in the auto range [`chartConsistency.test.tsx`] | `priceLines` + S/R `zones` (`:459-468`) | price lines, zones |
| Series markers | – | ✓ `createSeriesMarkers`, snapped to bricks (`:469-479`) | ✓ |
| Crosshair readout | – (time label only, `crosshairTimeLabel`) | ✓ OHLC, volume and every indicator value (`:600-622`) | – |
| Header (LTP, change, live) | `LiveChip` [`liveChip` tests] | price, change %, LIVE / stale badge | – |
| Paging | ✓ `onLoadOlder`, view kept; ChartsPageV2 "Loading older history…", **no "No older data"** | ✓ with "No older history" | – |
| Multi-chart linking | ✓ groups A–D: symbol / interval / crosshair (`MultiChartV2.tsx:128-131`, `charting/crosshairLink.ts`) [`multiChart.test.ts`, e2e `charts-v2:687`]; **no scroll link** | panes of one chart only | – |
| Theme | chart tokens + `data-chart-theme` App / Light / Dark, applied in place [`chartTheme.test.ts`, e2e `:230,378`] | `chartColors()` + hex fallbacks, on `THEME_EVENT` | `chartColors()` |
| Colour-blind | ✓ through the chart tokens (`tokens.css:177-178,216-217`) | ✓ up / down only; the indicator hex stays fixed | ✓ |
| Full screen | page-level `useFullscreen` (F / Esc, iPhone fallback) [e2e `:197`] | `fixed inset-0` toggle | – |
| Mobile | 44 px targets, Draw menu, sheets; touch options as ProChart | Tools sheet, phone hints | – |
| Session breaks / venue axis | venue zone on the axis | ✓ `SessionBreaksPrimitive` | – |
| Baselines | `charts-v2-*` (1440 / 390, light / dark, multi, watchlist, chartlight-on-dark) | none for ProChart | none |

- The fake library `chart/lightweightFake.fixture.ts` has no `priceScale().setAutoScale / options / setVisibleRange`, `scrollToRealTime`, `scrollPosition` or `resetTimeScale`. Extend it in the first PR. The gesture tests need it.
- The risky items, in order: R4 (the options owner), the gesture layer (R3), markers on ChartCore (they must not break the transparent-candle anchor for bars / line, `chartCore.ts:6-9`), and BacktestPage on ChartV2 (Supertrend / BB defaults from `strategyParams` have no v2 equivalent). For CC-5 the default is the four v2 overlays only, and the strategy's other indicators are listed as text.
- Migration order: options builder + R4 fix → markers + readout + Reset / Latest in the core → BacktestPage under v2 → delete CandleChart. `chartConsistency.test.tsx:159-176` stays green throughout, because the legacy pages keep ProChart.

## Part 2: interaction (notes)

What lightweight-charts 5.2.1 gives natively, and what needs our code:

| Gesture / control | Native | Our code |
|---|---|---|
| Drag the plot = pan time | ✓ `handleScroll.pressedMouseMove`, `horzTouchDrag` | – |
| Drag the plot vertically = pan price | only when auto-scale is already off | R3 gesture layer (auto off + `setVisibleRange`) |
| Drag the price axis = stretch | ✓ `axisPressedMouseMove.price` (turns auto off itself) | update the "A" state |
| Drag the time axis = zoom | ✓ `axisPressedMouseMove.time` | – |
| Wheel = zoom around the cursor | ✓ `handleScale.mouseWheel` (deltaY → `zoomTime(clientX)`) | – |
| Shift + wheel = pan | partly: the library pans on `deltaX`. Chrome / Edge / Safari turn Shift + wheel into deltaX; a mouse wheel in some Firefox setups does not. | a wheel listener: `shiftKey && deltaX === 0` → `scrollToPosition` |
| Pinch = zoom | ✓ `handleScale.pinch` (time only) | price pinch is not native (not asked) |
| Kinetic scroll | ✓ `kineticScroll.touch` (on); mouse off | – |
| Long-press crosshair | ✓ tracking mode; the default exit is `OnNextTap` | set `trackingMode.exitMode = TrackingModeExitMode.OnTouchEnd` ("lift ends it"); the readout is ours (R2) |
| Double-click / double-tap an axis resets it | ✓ (`doubleTapEvent` maps to the same handler) | – |
| Reset | ✓ `timeScale().resetTimeScale()` + `priceScale.setAutoScale(true)` + `scrollToRealTime()` | the button and clearing the memory |
| "→ Latest" | ✓ `scrollToRealTime()`, `scrollPosition()` | visibility from the range event, rAF-throttled |
| Log / % / Invert | ✓ `PriceScaleMode.Logarithmic / Percentage`, `invertScale` | settings UI; % turns manual price off (R3). Drawings convert through `coordinateToPrice`: test them in log mode. |
| Page vs chart on a phone | ✓ `vertTouchDrag:false` already lets the page scroll vertically. The option is read live, so it can be toggled. | the activation state; `touch-action: none` + `overscroll-behavior: contain` on the full-screen plot (the library sets no `touch-action`) |
| Landscape = full screen | – | `matchMedia("(orientation: landscape) and (pointer: coarse)")` on the Charts page → `useFullscreen` (iOS has no orientation lock; the existing fallback covers it) |

- **Embedded "tap to activate".** Horizontal drag and pinch already reach the embedded chart today, and a vertical swipe scrolls the page. Activation is therefore needed only for vertical / price moves. Risks:
  - Accessibility. The activator must be a real control: `tabindex=0`, `role="application"`-free, `aria-describedby` hint, Enter / Space activates, Esc deactivates, focus is not trapped. The "subtle outline" needs 3:1 against the card (use `--brand`).
  - The 3 s idle timeout. Deactivating while a finger is down would cut a gesture. Count idle from the last pointer-up, never during a touch.
  - Discoverability. The first-time hint ("Tap to move the chart") is stored per device and shown once.
  - Tests. Playwright's `touchscreen` taps only. Pinch and long-press need CDP `Input.dispatchTouchEvent` (Chromium projects only, so skip WebKit for those). The canvas cannot be asserted by pixels, so the core exposes its state on the root (`data-autoscale`, `data-active`, `data-latest-visible`, `data-crosshair`) for the e2e assertions.
- **Activated vs `setPanning`:** see R4. One owner.
- **History (2.4):** most of it is already core behaviour (above). Missing: "No older data" on ChartsPageV2 / EmbeddedChartV2 (`useChartCandles.ts:161` already returns `exhausted`), and an edge mark instead of the text row.
- **Multi-chart scroll link (2.6):** cells can have different timeframes, so link by **time** (`getVisibleRange` / `setVisibleRange`), not by logical range. Use a re-entrancy guard like ProChart's `syncing` (`ProChart.tsx:351-361`). The default (CC-7) is on only within a group that links the symbol and the interval. Needs `GroupSync.scroll` on the backend (see Migrations).
- **Performance (2.7):** native panning of 5,000 bars is not the risk. The risks are:
  - `ChartV2` calls `setData` on **every live price** for nine series and recomputes every indicator over all the bars (`ChartV2.tsx:253,288` → `chartCore.ts:269-303`). Use `series.update()` for the forming bar.
  - The per-frame range subscribers: `nearStart`, Latest, memory and linking all need rAF throttling.
  - The canvas primitives (drawings, layers, pane labels).
  - Measure with Playwright at 360 px with 4x CPU throttling (CDP `Emulation.setCPUThrottlingRate`): an rAF frame counter during a scripted 2 s pan, target ≥ 50 fps on CI (60 on a device). This also closes the open question CH6-2 ("not measured yet: drawing in a real browser").
- **Labels (user-friendly rule):** "A" alone is jargon for a beginner. Show the icon with the text "Auto" and the tooltip / aria-label "Fit prices to the screen" (CC-6).

## Part 3: colour (notes)

**Tokens: what exists vs the order.**

| Order token | Today | Missing |
|---|---|---|
| brand | `--brand`, `--brand-strong`, `--brand-fill`, `--brand-fill-strong`, `--on-brand` | – |
| up / down + colour-blind | ✓ (`tokens.css:22-23,55-56,73-74`) and chart copies | – |
| warn | `--warn`, `--warn-tint` | – |
| range (amber-yellow) | none (only warn) | `--range` (track) and `--range-mark`. Not an alias of warn: a 52-week range is not a warning. |
| info (teal / sky) | `--info` (sky; colour-blind → neutral grey); `--signal` (teal) | `--info-tint` (or keep `/10` alpha as `Badge` does) |
| ai (violet) | `--ai`, `--ai-2` (already used in 20 files, mostly Copilot) | – (a usage rule only, R5) |
| paper (orange) | none; `--armed` orange means LIVE / armed | see R6 |
| surface-1/2/3 | ✓ plus `--surface-inset` | value change only (R8) |
| data palette (call OI, put OI, IV, PCR, payoff) | partly: `chart-ov-1..4`, `signal`; the payoff uses up / down | `--data-call`, `--data-put`, `--data-iv`, `--data-pcr` (or names by role), with colour-blind values |

**Contrast** (WCAG, computed from the token values against `surface` / `surface-1` / `surface-2` / `surface-3`):
- Dark: every candidate hue passes 4.5:1 for text on every surface: sky 7.1–9.1, violet 5.6–7.2, teal 8.2–10.5, orange 6.8–8.7, amber 7.1–9.1. The only existing tight pair is `down` on surface-3 at 4.06.
- Light: violet-700 (`--ai`) 5.7–7.1 and sky-700 (`--info`) 4.8–5.9 pass. Teal-700 (`--signal`) is 4.41 on surface-3. Orange-700 is 4.17–5.18. Amber-700 (`--warn`) is 4.04–5.02, already below 4.5 on surface-3. Amber-600 (2.6–3.2) and teal-600 (3.0–3.7) fail as text. **Yellow-600 (2.4–2.9) fails even the 3:1 for UI parts.** So the "yellow range bar" in the light theme must be a darker amber track (which looks brown-orange, and again close to warn and orange) or a neutral track with a coloured marker. Default CC-4: a neutral track with a `--range` marker and the numbers in text.
- White text on a solid fill: orange-600 3.56 (fails), orange-700 5.18, violet-700 7.10, teal-700 5.47.

**The v2 colour check.** It already forbids raw hues, white / black, arbitrary colours and literal colours in style / SVG attributes (`check-v2-colours.mjs:26-42`), so the order's "forbid raw hex" is already the rule. Add new token names to the hint text only. The "one solid brand button per screen" heuristic **cannot** be static (a page composes many files: 20 v2 files hold a solid brand class, and BrokerPage can show two at once, Renew and Connect again, `broker/BrokerPage.tsx:60,63`). Make it a Playwright helper that counts visible elements whose computed background equals `--brand-fill`, called in the screenshot tests and reporting a warning (annotation), never a failure (CC-8).

**Shared components today.**
- `Button` (primary / secondary / ghost / danger, `components/primitives/Button.tsx:4-13`), with hand-copied class strings beside it: `trading/PartsV2.tsx:15-17` (`BUTTON_PRIMARY/SECONDARY`), `broker/parts.tsx:45` (`BTN_PRIMARY`), `admin/AdminSectionsV2.tsx:20`.
- `Badge` (neutral / up / down / warn / info / brand, `primitives/Feedback.tsx:22-34`).
- `ChipV2` (outline / solid / brand / warn, `dashboard/CardV2.tsx:72-79`).
- Two different `StatusChip`s (`settings/SettingsIndexV2.tsx:49`, `signals/SignalPartsV2.tsx:39`), `LiveChip`, `Signed`.
- **There is no RangeBar.** The 52-week and day ranges are text facts (`fno/OverviewV2.tsx:75-76`).
- 3.4 should therefore *extend* `Button` (add `outline`; v2 sizes h-11 / md:h-9) and replace the class-string copies, merge `ChipV2` and `Badge` into one `Chip` with tones, and add `RangeBar` new. It should not create parallel components.

**Colour-blind.**
- Every new meaning token needs its colour-blind values: today only up / down / info / signal / armed have them.
- The PA layer hex (`charting/priceAction.ts:109-110`) and ProChart's indicator hex ignore colour-blind mode. Fix that when they are brought to v2.
- Check the rule "every meaning keeps a distinct hue" (`tokens.css:76-77`) in a test across warn / range / info / ai / down for both themes.

## Fit with CLAUDE.md

- User-friendly: fine with CC-6 (no bare "A"), plain hints, no technical words. Log / % / Invert go in the chart settings popover under an "Advanced" group.
- `ui_v2`: gestures in the core only (v2). The tokens are scoped (R8). ProChart is unchanged for ui_v2 off.
- Keep what is built: the paging, view keeping, crosshair link, full screen and tokens already exist and are extended (R1, R7). No parallel components.
- Calm principle: R5 and R6 keep colour meaning-only. That matches "make the important things stand out".
- Aside, not part of this order: the signal chart labels single prices "Exit if wrong" / "Exit at profit" (`signals/signalText.ts:42`, `SignalPartsV2.tsx:121-124`). Check this against the signal-language rule (areas as ranges) in the formatter work, not here.

## Questions for Abhi (provisional defaults)

| # | Question | Provisional default |
|---|---|---|
| CC-1 | Status chips: green "Connected" and red "Error" (the order) vs §8 "success neutral with a check, errors amber"? | Neutral + check for Connected; warn for Needs action; down only when money or an order is at stake; info for tips. Recorded in DECISIONS as superseding §8. |
| CC-2 | PAPER in orange? | No: the neutral outline "Paper" chip stays (orange is LIVE / armed, and loss in colour-blind mode). |
| CC-3 | A deeper navy dark theme for v2 only, or for the whole app? | v2 only (scoped attribute). The chart background moves only after the comparison page. |
| CC-4 | Range bar look in the light theme (yellow fails contrast) | A neutral track, a `--range` marker, low / high as text. |
| CC-5 | "Test a strategy" on the v2 chart: its Supertrend / Bollinger / ADX defaults | The four v2 overlays; the strategy's other indicators named under the chart. |
| CC-6 | The Auto button label | Icon + "Auto", tooltip "Fit prices to the screen". |
| CC-7 | Multi-chart scroll link default | On within a group that links both the symbol and the timeframe; off otherwise. |
| CC-8 | One-solid-button heuristic: warning or failure? | A warning annotation in the e2e run. |
| CC-9 | Embedded charts: an "expand" button next to the existing "Open in Chart tab" link? | One control: "Expand" opens the same chart full screen in place. The Chart-tab link moves into it (no two buttons for the same need). |
| CC-10 | Log / % / Invert: per device (like the zoom memory) or saved on the server with the chart settings? | On the server, in `ChartPrefs` (one settings place; a backend field, no migration). The zoom stays per device. |

## Recommended PR sequence

One draft PR each, behind `ui_v2`, with tests. No migration in any of them.

1. `claude/chart-core-options`: `chart/chartOptions.ts` used by ChartCore and ProChart; the R4 fix (one options owner, `setPanning` via the core) with its test; the extended fake library; CandleChart types → `charting/markers.ts`, component deleted. No visual change (baselines unchanged).
2. `claude/chart-core-axes`: the gesture layer (R3), Auto / Reset / → Latest floating toolbar (44 px phone targets, tooltips), Shift + wheel, `trackingMode` OnTouchEnd + the crosshair readout, unit tests for the gesture state, Playwright drag / axis tests at 1440 / 390.
3. `claude/chart-core-markers-backtest`: series markers in ChartCore; "Test a strategy" on ChartV2 under v2; ProChart stays for ui_v2 off.
4. `claude/chart-phone-gestures`: full-screen `touch-action`, embedded activation (a11y as above), first-time hint, landscape full screen, long-press / pinch e2e via CDP (Chromium).
5. `claude/chart-history-memory`: "No older data" edge mark, `update()` for the live bar, zoom memory (R9), Log / % / Invert (CC-10, with the backend field), the 5,000-bar perf test.
6. `claude/chart-multi-scroll-link`: `GroupSync.scroll` (backend + frontend), time-based linking.
7. `claude/colour-tokens`: new tokens in `tokens.css` (four variants, v2-scoped surfaces per CC-3), tailwind names, `tokens.test.ts` pairs and the distinct-hue test. `gen-palette.mjs` untouched.
8. `claude/colour-components`: `Button` outline + v2 sizes (replacing the class-string copies), one `Chip` (merging `ChipV2` / `Badge`), `StatusChip`, `RangeBar`; Storybook; the one-solid-button e2e helper (warning).
9. The comparison page for Abhi (before / after: Today, Option chain, Signals, Positions, Charts, Settings; 390 / 1440; light / dark), then page adoption PRs only after his OK, baselines refreshed per PR.

## Part 3 build notes

- The owner accepted CC-1..CC-14 as proposed (DECISIONS 23 Oct, #434); the build follows those defaults.
- **PR 1 `claude/colour-tokens` (item 7).** `tokens.css` gains `--range` / `--range-track` (CC-4: a neutral track and an
  amber-yellow marker, not an alias of warn), `--info-tint` and `--ai-tint`, in the dark, light and both colour-blind
  blocks; Tailwind names `range`, `range-track`, `info-tint`, `ai-tint`. The deeper navy-slate dark surfaces
  (surface, surface-1/2/3, inset, border, border-subtle, glass) sit under `:root[data-shell="v2"]:not([data-theme="light"])`
  (CC-3): `AppShellV2` marks only its own `<div>` today, so nothing changes until the pages adopt the look. The chart
  background does not move. Colour-blind mode: warn moves to a yellow / dark olive (CC-20), the AI violet is lighter
  in the dark theme. `ui/tokens.test.ts`: AA pairs for the new tokens in six variants (incl. navy and navy colour-blind),
  the existing pairs on navy, "no normal-theme value changes", and the distinct-hue test (OKLab distance, after a
  deuteranopia simulation in colour-blind mode; up / down / warn / info / ai pairwise, range against all but warn).
  `gen-palette.mjs` untouched.
- **PR 2 `claude/colour-components` (item 8).** `Button` gains `outline` and the v2 size (`h-11 md:h-9`); `buttonClass()`
  is the one class source, and `trading/PartsV2.tsx` `BUTTON_PRIMARY` / `BUTTON_SECONDARY` are now its output (same
  classes plus `disabled:cursor-not-allowed`). The broker and admin class strings differ in size and focus (no `md:h-9`
  on the broker buttons), so they stay until their pages adopt `Button`. `ui/Chip.tsx`: one chip (tones neutral /
  brand / up / down / warn / info / ai; tint / outline / solid; pill / badge); `ChipV2` and `Badge` keep their exports
  and render byte-identical markup (pinned). `FilterChip` (selected = brand outline + tint). `ui/StatusChip.tsx`
  (connected / needs-action / error with `atStake` / info / paper / live). `ui/RangeBar.tsx` (`role="meter"`, low /
  high as text). `e2e/solidBrand.ts`: counts the visible buttons and links painted with `--brand-fill` and adds a
  `warning` annotation above one; called from `afterEach` in the Today, Option chain, Signals, Positions, Charts and
  Settings specs. The two page-level `StatusChip`s (Settings, Signals) stay until adoption.


## Colour v2 (the redo after the owner's 23 Oct review)

The owner did not approve the first comparison: the after looked like the before, and Settings lost colour (INBOX and
DECISIONS 23 Oct). The rules stay (ATP blue is the one solid main button, colour by meaning, no gradients, no card with
many colours, CC-1 / CC-2, AA text and 3 : 1 UI parts, colour-blind mode, light and dark). His six additions are built
behind a new flag `colour_v2` (OFF, needs `ui_v2`), so nothing changes for users until he approves the new comparison.

- **Tokens** (`tokens.css`, new names only): `brand-tint`, `up-tint`, `down-tint`, `teal-accent` (a category teal, the
  same hue as `signal`), `soft-brand / -ai / -warn / -down / -up / -teal / -info` (the icon circles), `chain-call`,
  `chain-call-itm`, `chain-put`, `chain-put-itm`, `oi-call`, `oi-put`, `nav-insights`. Colour-blind: every red / green
  token follows down / up to orange / blue, teal follows signal. The dark theme's layered surfaces (a deeper page,
  lighter cards with a visible outline and a firmer shadow) sit under `:root[data-colour="v2"]:not([data-theme="light"])`,
  set on <html> by the v2 shell only while the flag is on. The chart background does not move.
- **Shared pieces**: `ui/colourV2.tsx` (the switch, read once in AppShellV2 and shared by context; off outside the
  shell), `ui/IconBadge.tsx`, `ui/DirectionChip.tsx`, `ui/Sparkline.tsx`, `shell/navTone.ts`; StatusChip's check is green
  (the owner's words: "status chips stay neutral, but the check icon is green"; the chip itself stays neutral).
- **Pages** (flag on): Settings rows in soft circles (Broker blue, AI violet, Alerts amber, Risk limits red, Appearance
  teal, Trading defaults sky, Account / Team / Help / Setup / Advanced neutral), neutral chips with a green check / amber
  needs action; Option chain call / put tints, ITM a step stronger, red / green OI bars (soft fill + a solid edge),
  a brand-tint ATM row with the "ATM" word in the text colour (brand text on the tint is under 4.5 : 1); Today's P&L
  tint by sign, Needs attention icons by type (approval brand, paused / login / warning amber, alert teal, red for a
  failing bot, the loss lockout and a high exposure - CC-1), sparklines from the broker's real 5-minute candles (the
  old Market pulse's source; nothing from market memory, never sample data); Long / Short chips on Signals, Today and
  My trades, "Waiting for you" amber; navigation icons by group (Broker brand, Markets info, Trading teal, Insights
  magenta, AI Copilot violet, the rest neutral), the active bottom-bar tab on a brand-tint pill.
- **Tests**: `tokens.test.ts` "Colour v2 tokens", `ui/colourV2.test.tsx` (every page on and off), the flag test; e2e
  baselines unchanged with the flag off.
