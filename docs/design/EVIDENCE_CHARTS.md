# U8 EV1 + EV2: evidence charts (Phase 0 item Z7)

Spec: `docs/specs/ATP_SCREEN_EVIDENCE_CHARTS_ADDENDUM.md` (U8 §0-§6), Phase 0 freeze Z7 ("minimal: only the screen's
conditions; verify strip; Telegram images"). Flag `evidence_charts` (off by default). Branch `claude/z7-ev12-evidence`,
stacked on Z7a-3 (zone band selector) with U6 BK1 (breakout screens) merged in.

Informational only. A pack explains how a screen's conditions evaluated on the data shown. It never places, proposes
or ranks an order. Every text ends with "Not a recommendation."

## 1. The evidence pack (`evidence/1`, `app/screener/evidence.py`)

`build_pack(ast, validated, data, base_tf, params, as_of, window, debug, constants)` uses the runtime's own evaluator
(`runtime._Eval`). That is the same code `run_screen` runs, on the same `SymbolData`, parameters and cross-sectional
constants. Every number is the engine's number at the matched bar, not a re-computation (value parity is tested).

- **No look-ahead.** Bars that close after `as_of` are dropped before anything is evaluated, on every timeframe. The
  zone timeline is read at the bars' closes. The matched bar is the last bar kept (`matched_index`). Appending or
  changing later bars, or adding a later zone snapshot, leaves the canonical JSON identical (tested).
- **Blocks.** A top-level AND is a list of blocks (the U5 funnel's stages); anything else is one block. Each block
  holds its conditions: AND / OR groups and a NOT over a group are opened; a NOT over one condition stays one.
- **Rows (the verify strip).** One row per condition: its text, pass / fail, its terms and a one-line `verify` text
  built server-side, for example `close 103.80 > SMA(close, 10) 101.33`. A term is an operand's value at the matched
  bar, with its unit (the validator's typing) and the display text.
  - Breakout functions: close, the level ("range high" ...), the distance in ATR (ATR of the bar before, as the
    engine uses it), volume vs average, and the B2 / B4 / B7 type. All come from `breakouts.screens.evaluate_screen`.
  - Zone functions: close, the zone read (bounds, state) and the distance.
- **Drawables.** At most one per condition, in bar-index + price terms (engine-neutral):

  | Kind | Used for | Geometry |
  |---|---|---|
  | `overlay` | price comparisons | the compared series (at most 2) and literal levels on the price pane |
  | `pane` | non-price comparisons | the series on a lower pane: `volume` (histogram) or `oscillator` (with its threshold line) |
  | `box` | breakout base | from / to bars, low / high and the level |
  | `band` | zone | the zone a zone condition read at the matched bar, through the Z7a-3 `select_bands` (at most 6, one band per zone however many conditions read it) |
  | `bars` | pattern candles, B13 | the candles a pattern reads (1-3), the B13 bar with its midpoint level |

  Other filters and category conditions (`Trend`, `IsFnO`, `NearSupport`, a value on another timeframe or an earlier
  bar) are in the strip only, with a note. Their multi-timeframe strip is EV3.
- **Failed conditions.** A condition that failed (inside an OR, or any condition on a symbol that did not match) is
  drawn only with `debug`, marked `failed`. Stored packs are built with debug. `evidence_store.view()` removes failed
  drawings (and caption lines) unless the reader asks for debug ("Show failed conditions").
- **Bounds.** Window 120 bars by default (at most 300). At most 24 conditions; the rest are counted in
  `summary.not_shown`. At most 2 series and 2 levels per drawable. The JSON is at most 256 KB; the window halves until
  it fits.
- **Owner rule 0.** Only the pack's drawables are drawn. There are no user layers, no other indicators and no zone the
  screen did not read. Tests check that every drawn series is one of the screen's operands. A screen without zone
  functions draws no zone even when the symbol has zones loaded. The "Show my layers" toggle is not built
  (OPEN_QUESTIONS EV-5).

## 2. Where packs are made and kept (`app/screener/evidence_store.py`, table `screen_evidence`)

- `POST /api/screener/run`: for the MATCHED symbols (the first 25), in the same pass. The packs use the same universe
  `SymbolData` (zone timelines attached) and the same ranks (`_cross_sectional` over the same universe). They are
  built in a worker thread (`asyncio.to_thread`) and stored in a savepoint. A failure is logged and skipped; it never
  fails the run. Each result row gets its `evidence_id`, which is also stored in the run's `result_json`. A preview
  (`preview: true`) stores nothing.
- Bar-close alerts (`alerts/barclose.evaluate_rule`): one pack per fired event (at most 10 per evaluation), from the
  closed frames the rule was just evaluated on. Never on an intrabar rule. The `alert_event_id` is kept, with the
  event's own bar label.
- The record holds the canonical JSON (sorted keys) and its SHA-256, tenant, user, run or alert event, symbol, bar time
  and schema. Retention: `RETENTION_SCREEN_EVIDENCE_DAYS` (default 90). Migration `e8a1c4d2f9b7` is reversible.

## 3. Reading (tenancy, BT1)

`GET /api/screener/evidence/{id}` (`?debug=`), `/{id}/image.png` (`theme=light|dark`),
`/runs/{run_id}` (ids by symbol) and `POST /{id}/telegram`.

- Behind `screener_v2` (503 while off, like the whole screener) and `evidence_charts` (404 while off: hidden).
- The record's organisation only; another organisation gets 404 (tested).
- BT1: with `byob_data_tier` on, only the user whose run or alert rule read the bars can read the record. A broker
  entitlement is personal (`docs/BROKER_DATA_TERMS.md`). With the flag off, the organisation's members can read it
  (that is today's run visibility). Sending is always the owner's own: `can_send` is true only for them.

## 4. Images (EV2, `app/screener/evidence_png.py`)

- The Vision chart's renderer (`app/ai/chart_png.py`): a standard-library PNG, no network, no clock and no font files.
  It adds a fuller 3x5 bitmap font. Characters it cannot draw (another script) become "?"; the caption carries the
  exact text. The same pack gives the same bytes. The test pins the SHA-256 of the raw pixels, which does not depend
  on the zlib build.
- Drawn: the bars, the matched bar (dashed line and marker), and the drawables that define the signal. Order: bands
  and boxes, then candles, then overlays, then at most ONE lower pane, at most 4 in all. The rest are in the caption.
- Colours: Okabe-Ito. Rising candles are hollow and falling ones filled, so direction never depends on colour.
  Light and dark themes. The image is capped at 512 KB.
- Caption: symbol, timeframe, bar time (IST), the screen, each shown condition's verify text with ✅ / ❌, and the
  informational note. Whole lines only, within Telegram's 1024 characters, with the note always last.
- Telegram goes only to the record owner's OWN linked private chat (S3b-2 `notification_links`), through the
  organisation's bot. It never goes to the organisation's group chat, because the bars may come from that user's own
  broker session. The bot token never appears in an error.
  - "Send to my Telegram" (`POST /{id}/telegram`) sends it from the app, rate-limited to 10 per minute.
  - Alerts: after a screen alert's text reaches the rule creator's linked chat, the dispatcher sends that
    notification's evidence images (at most 3). This is best effort: an image problem is logged, never retried, and
    never fails or repeats the text delivery. With the flag off, alerts stay text only.

## 5. Frontend (`frontend/src/screener/`)

- `evidence.ts` holds the types, the API and the pure geometry (`evidenceGeometry`, `visibleDrawables`, `verifyRows`).
- `EvidenceChart.tsx` is an SVG chart: role img, a title and a description that names what is drawn. Colours come from
  the theme tokens (light / dark). Drawables use the colour-blind palette.
- `EvidencePanel.tsx` is a wide dialog. It holds the chart and legend; "Why it matched" (a toggle per block, and
  hovering a block picks out its drawing); "Show failed conditions" (re-reads the pack with debug); the verify strip
  (a table with scoped headers and screen-reader text for passed / did not pass); and "Send to my Telegram".
- `ResultBoard` shows "Show on chart" on matched rows that have an `evidence_id`. While the flag is off there are no
  ids and no button.
- Fixture: `evidenceSample.json` is the backend's own pack (test `test_the_frontend_sample_is_the_backend_pack`;
  regenerate with `UPDATE_EVIDENCE_SAMPLE=1`).

## 6. Tests

- Backend `tests/test_u8_evidence.py` covers:
  - only the conditions are drawn;
  - debug-only failed drawings;
  - value parity of rows, terms, blocks and series with `runtime.evaluate`, and `matched` with `run_screen`;
  - no look-ahead (mutated or appended bars and a later zone snapshot);
  - breakout parity per function, with box and terms from the engine;
  - zone bands through the selector, at most 6;
  - rank parity over the universe;
  - image determinism (pixel hash), the layer present (pixel diff and colour), the dark theme, one lower pane;
  - caption limit, Devanagari name and no advice wording;
  - size bounds and IST bar times;
  - the frontend fixture.
- Backend `tests/test_u8_evidence_api.py` covers:
  - flag off: hidden, 404, nothing stored;
  - one pack per matched symbol, stored with the run;
  - another organisation gets 404;
  - BT1 teammate: 404 with the flag on, no send ever;
  - Telegram to the own chat only, no token;
  - an alert stores its pack, and its image goes to the creator's chat only, after the text;
  - flag off: text only;
  - retention;
  - a builder failure never fails the run.
- Frontend: `evidence.test.ts` (geometry, filtering, colours, IST, summary) and `EvidencePanel.test.tsx` (accessible
  SVG, nothing outside the pack, theme tokens, verify strip, failed toggle, Devanagari name, Show on chart), plus the
  story `Screener/Evidence chart`.
