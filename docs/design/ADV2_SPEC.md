# ADV-2 - Advanced analysis that reads like a trader (owner 4 Nov: "A B C D sagale kara")

Owner audit (4 Nov, stock Daily): ATP's chart showed the candles, but every section of the reading was empty or negative
("data not ready" x4, "-", "Not read yet", "None ..."). Causes (read-only audit): the reading used only ATP's stored
candles (full history only for 4 indices; MIN_BARS 30 per timeframe), areas came only from the nightly zones job's list,
the UI rendered every section even when empty, and the content was a thin table. Sample approved: the 390 card built
from ATP's own reader on real NIFTY Daily candles (trade-data, to 23 Oct 2026).

## A - one candle source (backend `app/chart_analyze/hybrid.py`)
- Per timeframe (the picked one and the top-down W / D / 1H / 15M rows): ATP's stored candles (`load_frame`, unchanged)
  when they hold at least MIN_BARS; else the user's OWN broker candles for the same window, read only, through the
  existing market-data read path (the chart's candle route / entitlement `resolve` with the user's own session - never
  another user's, never another tenant's). The shared lake is never written from these candles (licence).
- Windows: as today (`pal.DEFAULT_DAYS` / `MTF_DAYS`), capped by what the broker serves.
- The reading says where its candles came from: `reading.source` = "stored" | "broker" | "stored+broker" and
  `reading.bars_read` (picked timeframe).
- No usable broker session and too few stored candles: the item fails with ONE positive sentence naming what works
  (same style as `unavailable_message`), never an empty result.
- No look-ahead: closed candles only, as today.

## B - areas for the picked symbol on the spot
- When the zones job has no bands for the symbol (or zones are off), compute the bands for this read from the same
  frames with the zones engine's own pure functions (no new rules, no new numbers; settings = the platform's zone
  settings). Not stored (a read-time result), cached like the layer.
- `areas[]` gains `distance_pct` (distance / last close x 100, 2 decimals).

## D - depth (contract additions on `reading`, all optional for old stored results)
- `swings`: the last up to 6 swings of the picked timeframe `[{label: HH|HL|LH|LL|H|L, price, time}]` (from the layer).
- `state`: `{phase: <layer structure_state.state>, text}` - e.g. "Down - pullback running".
- `protected_distance_pct`, and `last_break` as today.
- `higher_degree`: `{trend, text}` from the layer's larger degree (shown behind (?) only).
- `scenarios`: `{main, other}` - two sentences built in code from the structure only (state, protected, last break,
  nearest areas); structure words only: never buy / sell / target / stop-loss wording, never a trading rule the owner
  did not state; checked by the existing signal-language guard.
- `watch`: up to 3 `[{when, meaning}]` - e.g. {"Price at 23,116-23,175", "handover - commitment"},
  {"Daily close above 23,175", "structure changes"}, {"Close below 22,183", "new LL"}.

## C - no empty or negative sections (frontend `HybridResultV2`)
- A section with nothing in it is not rendered. A checklist row is shown only when present (with its fact);
  absent rows are counted in one line behind Details ("4 of 7 checks present").
- No "-", "Not read yet", "Not formed yet", "None ...", "data not ready" placeholders; a timeframe row without
  enough candles is left out of the Structure table.
- Layout = the approved sample: header chips (label + state), chart, Structure (trend, swings, protected + %, last
  break, higher degree behind (?)), Areas of interest (above / below, band, tag, %), Scenarios, What to watch, the
  disclaimer. Numbers first; explanations behind (?) / Details; tokens only; phone 390 first.
- The picture's note ("ATP does not know your picture's price scale ...") is reworded positively and moved behind
  Details with the download.

## Proof
vitest + pytest for the contract and the fallback (fake broker), e2e at 390 / 1440 with a mocked hybrid result
(stored and broker cases), before / after screenshots on one comparison page. No migration.
