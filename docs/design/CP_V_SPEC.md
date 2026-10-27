# CP-V fixed spec (Copilot chart reading) - for the owner's ✅

Second, independent review (owner order 26 Oct, review/design session) of `docs/owner-orders/ATP_ORDER_CP_V.md`
(CV1-CV14) against `origin/main` 232bf03 (26 Oct). The first review (`CP_V_REVIEW.md`, 24 Oct) was re-checked claim by
claim; this file adds what changed since, what it missed, and the build spec with every finding applied. The owner's
order is not edited (CLAUDE.md §8); this spec is what the build follows once ✅. No code change.

## 1. First review re-checked (24 Oct claims vs code today)

| Claim (CP_V_REVIEW.md) | Today | Evidence |
|---|---|---|
| `complete_full` takes one image | holds | `ai/providers.py` l.338 / 420 / 553 / 729 (`image_png: Optional[bytes]`) |
| `chart_png` draws entry / stop / target, 800x450, max 120 bars, no volume | holds | `ai/chart_png.py` l.5, 17-19, 132 |
| `vision_audit` strips digits from model notes | holds | `ai/vision_audit.py` l.160-165 |
| chart_analyze Hybrid returns 409 | holds | `chart_analyze/service.py` l.125-126 |
| Gap 9: the price-action layer reads only the shared lake, never broker rows | **outdated** | DATA-H added `market_lake/own_bars.py`: `price_action_layer.load_frame(..., private=(tenant_id, user_id))` reads the user's own private broker bars (`own:<label>`) when they hold more bars, with DH3 auction flags applied (l.342-354) |
| Gap 1: DATA-H not built | **partly outdated** | own bars, auction flags (`md_bar_flags`), backfill modules exist; DATA-H still "in progress" (ROADMAP) - lake fallback usable only where health says OK |
| Weekly bar: forming week left out | holds (look-ahead safe) | `charts/price_action_concepts.py` l.211-222 |

## 2. New findings (not in the first review)

| # | Finding | Risk | Fix in this spec |
|---|---|---|---|
| F1 | One bar loader exists now: `load_frame(private=...)` gives the user's own broker bars or the shared lake, one source per frame, auction flags applied. | Building a second loader (`chart_read/data.py` as planned) would duplicate it and let vision and code read different bars. | CV1 uses `load_frame(private=)`; a live broker top-up only for the bars after the stored ones. The SAME frames feed the image, the OHLC table, the validator and the engine (CPV-11 now cheap). |
| F2 | The organisation path ignores `need_vision`: `resolve.provider_for_user` checks vision only on the user's own key; `ai_settings.provider_for` (org / rule-based) has no vision argument. | An image sent to a text-only org model -> an error or a reply that never saw the chart. | CPV-C: refuse before the call when the resolved provider/model is not in `VISION_PROVIDERS` / `VISION_MODELS`: "Your AI cannot read images" (CV6 style). |
| F3 | The order says "Claude vision"; `VISION_PROVIDERS` also holds free routers (OpenRouter free, Cloudflare Llama 4 Scout) and Mistral. | Quality of the reading varies a lot; a free router may change model per call (cache key unstable). | CPV-15 (owner): any vision model, its name on the label, free routers excluded; or Claude only. |
| F4 | DH3: a Daily bar whose extreme was set only in the closing auction is read with the continuous-session extremes; the official close is kept. | The validator could reject a true price (or accept an auction-only extreme the reader ignores). | Validate against the flag-applied frame actually sent; the table marks such bars. |
| F5 | Prices quoted by the model are still free numbers (CV3 / CV9). | Exact-match after tick rounding (CPV-7) fails on harmless rounding and passes a lucky coincidence. | The model cites a bar, not a price: `{tf, bar_time, field: high/low/open/close}` (zone = two such refs). Code looks the price up. Nothing to round; a missing ref = "not confirmed". Supersedes CPV-7's mechanism (same intent). |
| F6 | Per-user daily read count needs no new table: `llm_calls` already has `user_id` + `feature` (`ai/metering.log_call`). | A parallel counter drifts from the cost log. | CV5 count = rows of feature `copilot_chart_read` for the user today (a cached read does not count). The cache still needs one table (migration, after 15:30 IST). |
| F7 | Overlaps: `ai_multiview` ("analyze <symbol>", four rules-only views, H-C4) and `vision_byok_v3` ("daily chart review", unfinished). | Two Copilot phrases answer the same ask differently. | CPV-16: "read the chart" -> CP-V when its flag is on; multiview stays for "analyze"; `vision_byok_v3` fate decided in P2-1. |
| F8 | Four images x 120 bars + four OHLC tables is a large prompt. | Per-read cost above the CV5 per-call limit on paid keys. | Estimate before the call (chart_analyze `_estimate` pattern); refuse with the number if over the per-call cost setting. OHLC table capped by the same bars-per-TF setting. |
| F9 | ADR-0006 and §3 wording: chart_analyze forces the paid-plan check only because private method text is sent. | Copying chart_analyze as-is would block free keys for no reason (CPV-3). | CP-V sends only the generic checklist: no paid-plan gate; if a private provider ever adds method text, the gate applies. |

## 3. Fixed spec (build follows this)

- **CV1 Data.** W / D / 1H / 15M closed bars via `price_action_layer.load_frame(private=(tenant, user))` (own broker
  bars else shared lake), then a live broker top-up of the missing recent bars through the user's own session. Per TF
  a status OK / SHORT / MISSING with bar count and first / last date. No read when every TF is MISSING.
- **CV2 Images.** `chart_png` extended in place: clean mode (candles + volume, no indicators, no entry / stop / target
  lines), last closed bar only. Four images in one call where the provider takes several (CPV-9), else a 2x2
  composite. Multi-image `complete_full(images=[...])`, single `image_png` kept for existing callers.
- **CV3 + CV9 Numbers.** The model returns short JSON per checklist item; every price is a bar reference (F5); code
  fills prices; free text has digits stripped (`vision_audit` rule). Failed items are dropped and listed
  "not confirmed"; the validation log is stored with the read. Label "read by vision + data" + the model name.
- **CV4** Information only; no write tool; tests assert the tool registry change is read-only (ADR-0006).
- **CV5 Cost.** Provider by `resolve.provider_for_user(need_vision=True)` + the F2 check; per-user daily reads
  (count from `llm_calls`, F6), per-call cost limit (estimate first, F8), "reads left today" shown, admin spend from
  the existing console by feature. Cache per symbol per closed bar (key per CPV-8), one table, migration yes.
- **CV6** Missing / short data stated per TF in plain words; never "structure: none".
- **CV8** Checklist v1 (generic, public default in settings, versioned): trend per TF; external swings; zones
  above / below (range, TF, fresh / tested); breaks and false breaks; location of price; confluence; next opposite
  zone (as its range, no points, no R:R - CPV-5); data gaps. Owner definitions / weights only via a private provider.
- **CV10** Annotated PNG drawn by our code from validated JSON only (bands by role, swings, break labels, trend arrow,
  price line); PNG + CSV download, disclaimer on the image; no share link (CPV-6).
- **CV11** Table: item · reading · evidence (bar refs shown as date + price) · confidence as a word · source.
- **CV12** One "area of interest" (range, never a single price), its factors in the table; disclaimer once.
- **CV14** Advanced = engine readings from the public layers (`price_action_layer`, `price_action/zones`, not
  `level_gate`) run on the SAME frames (F1); completeness per CPV-10; rows code / AI / both / disagree; manual switch.
  The P5-1 reader plugs in later through a provider boundary.
- **CV7 / CV13 proof**: run by the owner on production; images and logs kept in atp-private, not this repo.

PR plan (unchanged order, smaller A): **A** data = a thin wrapper on `load_frame` + top-up + TF status;
**B** renderer; **C** vision contract + F2 check + validator (migration: reads table); **D** limits + admin;
**E** UI behind `ui_v2` + flag `copilot_chart_read` (off); **F** Advanced. Build starts after DATA-H (ROADMAP).

## 4. Owner decisions (defaults used if no answer)

| # | Question | Default |
|---|---|---|
| CPV-15 | Which AI reads the chart (order says "Claude vision")? | Any paid vision model of the user's / org's key, its name on every read; free routers (OpenRouter free, Cloudflare) refused with a plain line. |
| CPV-16 | "read the chart" vs the existing "analyze <symbol>" (multiview)? | "read the chart" -> CP-V when `copilot_chart_read` is on; multiview unchanged. |
| CPV-17 | Model cites bars, code fills prices (F5) instead of exact price matching? | Yes (replaces the CPV-7 mechanism; the rule "only data prices" is unchanged). |

CPV-1 .. CPV-14 (OPEN_QUESTIONS.md) stand as written, except CPV-7 (mechanism per CPV-17) and CPV-11 (now via
`load_frame(private=)`).
