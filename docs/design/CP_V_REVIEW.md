# CP-V review: Copilot chart reading (vision + data) against origin/main

Independent review (CLAUDE.md §6) of the owner's order CP-V (`docs/owner-orders/ATP_ORDER_CP_V.md`, CV1-CV14, on the
branch `claude/owner-orders-data-h`) against the code on `origin/main` (24 Oct 2026). No code is changed here; the
open questions are CPV-1 .. CPV-14 in `docs/OPEN_QUESTIONS.md`. Checked against CLAUDE.md, DECISIONS.md (the DATA-H
and CP-V rows) and ADR-0006.

**26 Oct: re-checked against main 232bf03 - see `CP_V_SPEC.md` (gap 9 outdated by DATA-H own bars; new findings F1-F9; fixed spec).**

## (a) What exists to reuse

| Need (CV) | Exists on main | Where |
|---|---|---|
| Copilot chat with read tools (CV1, CV4) | Chat-first Copilot; read tools only (ADR-0006); `get_chart_structure` reads the price-action layer W / D / 1H / 15m from the shared lake ("bars in the lake"), `get_market_zones` the stored zones | `backend/app/ai/copilot_chat.py`, `backend/app/ai/tools/copilot.py` (`lake_key`, flag `chart_price_action`), `backend/app/ai/tools/market.py` (broker frames via the org / own session, never sample data) |
| User's own key, vision (CV2, CV5) | BYOK keys encrypted, opened per call for the owner only; daily / monthly USD caps (hard stop); vision models per provider (default Claude Haiku); `no_vision` error kind; `complete_full(..., image_png=...)` for Anthropic / OpenAI / Gemini; "one key" resolve (`ai_one_key`): user key, else org provider, else `ai_not_connected` | `backend/app/ai/byok.py`, `byok_routes.py`, `connect_routes.py`, `key_formats.py`, `providers.py` (~l.334), `resolve.py`, `core/config.py` ~l.200 (`vision_byok`) |
| Server-side chart image (CV2, CV10) | Deterministic PNG encoder, stdlib only, 800x450, max 120 bars, candles + price labels + level lines, SHA-256 of what was sent | `backend/app/ai/chart_png.py` (used by `vision_audit.py`) |
| Vision report validated against data (CV3, CV9) | Vision audit: digits in the model's notes dropped, numbers only from the engine's data, instruction-like notes withheld | `backend/app/ai/vision_audit.py` |
| Analyze a chart (P2-5) | Private-engine boundary (`atp_private.chart_analyze`, absent = "engine not installed"); generic output contract (bounded, filtered, disclaimer forced); per-tier analyses a day / month + monthly cost cap with advisory lock; storage, share, image sanitising; strikes from the user's OWN deployment settings; Hybrid mode (data path) planned as PR-B / PR-C, returns 409 today | `backend/app/chart_analyze/` (`provider.py`, `contract.py`, `limits.py`, `settings.py`, `engine.py`, `strikes.py`, `service.py` l.125), `docs/design/CHART_ANALYZE.md` |
| Metering and admin spend (CV5) | Every call in `llm_calls` with `cost_usd`, `billed_to_user_key`; plan budget fallback; per-minute AI rate limit; admin console AI spend per organisation and task | `backend/app/ai/metering.py`, `pricing.py`, `rate_limit.py`, `backend/app/admin/console.py` ~l.315 |
| Signal language (CV4, CV12) | One shared word list (buy / sell / target / stop / SL), server + frontend; Copilot banned words (recommend, best, guarantee ...); output filter | `backend/app/signals/language.py`, `chart_analyze/signal_language.py`, `ai/wording.py`, `ai/output_filter.py` |
| Candles W / D / 1H / 15M (CV1) | Broker candles: 1-minute base resampled, shared per-key fetch (LD-10), completed daily bars for W; lake: `candles_as_of`, weekly from daily, 1H / 15m rebuilt from 1m; closed bars only, as-of reads, fingerprint | `backend/app/market_data/service.py`, `shared_candles.py`, `backend/app/market_lake/asof.py`, `backend/app/charts/price_action_layer.py` |
| Engine readings for "Advanced" (CV14) | Causal swings per degree, real breaks, Dow trend per degree, top-down W / D / 1H / 15m (`price_action_layer`); stored zones with lifecycle fresh / tested / broken (`price_action/zones/`); level gate state machine (`price_action/level_gate/`) | public ports; the full Trade reader is P5-1 (not ported) |
| CSV / PNG export | `backend/app/exports/`; chart_analyze share PNG | |

## (b) Gaps

1. **DATA-H is not built on main** (no backfill / health per index per TF). CV1's lake fallback needs it; until then a
   short lake must be stated per TF (CV6), never "structure: none".
2. **Multi-image vision**: `complete_full` takes ONE image. CV2 wants one per TF (four). Needs a list of images per
   provider (Anthropic / OpenAI / Gemini accept several) or a 2x2 composite.
3. **Renderer**: `chart_png` has no volume pane, no zone bands, no trend arrows, no break labels, no "current price"
   line style; it draws entry / stop / target lines (must NOT be used for CP-V). Needs a clean mode (CV2) and an
   annotated mode (CV10), extended in place.
4. **Checklist + JSON contract (CV8, CV9, CV11)**: no versioned checklist setting, no per-item JSON schema, no
   validator of prices / times against the OHLC tables, no "not confirmed" list, no validation log (CV13).
5. **Per-user daily reads + remaining count (CV5)**: chart_analyze counts analyses per tier; BYOK caps are USD only.
   A CP-V feature key with "reads left today" is missing.
6. **Cache per symbol per closed bar (CV5)**: none for vision results.
7. **Copilot rendering**: the chat shows text / tool cards; images, the row table, PNG / CSV download are new UI
   (phone + desktop, behind `ui_v2`).
8. **"Complete reading" (CV14)**: no completeness signal exists; the price-action layer returns a trend even on few
   bars. Advanced mode needs an explicit check.
9. **Data source mismatch**: `price_action_layer` reads only shared-licensed lake sources and refuses `broker:*`
   rows; CV1 wants broker bars first. Code and vision would read different bars, creating false "disagree" rows.

## (c) Conflicts and risks

- **CP-V vs Analyze a chart (P2-5)**: overlap, not the same feature. P2-5 starts from a user's image and the PRIVATE
  method engine; CP-V starts from data, renders its own images and uses a generic checklist. CHART_ANALYZE's planned
  Hybrid (PR-B: broker data first, rebuild, match check) is CP-V's data path. Recommend one family: CP-V as a new
  mode `data_vision` reusing chart_analyze's limits / storage / sanitising / resolve / contract style, entered from the
  Copilot ("read the chart") and the Analyze page; the private method stays only in P2-5's engine (CPV-1).
- **Who pays for the vision call**: reuse `resolve.provider_for_user` - the user's own key first (their USD caps
  apply, `billed_to_user_key`), else the organisation's provider within plan budget + this feature's cost cap, else a
  plain "Connect your AI" line. Platform-paid reads need a per-user daily count anyway (CPV-2). Gemini free tier may
  train on input: the generic checklist is not secret, so no paid-plan gate is needed unless the private method is
  sent (CPV-3).
- **Public-repo rule**: the CV8 list (trend, external swings, zones, breaks / false breaks, location, confluence,
  room, data gaps) is generic price-action vocabulary and can live in the public repo as a settings default. The
  owner's weighting / definitions (what makes a zone "high quality", false-break rules) are method content: they
  belong in `atp_private` behind a provider boundary like `chart_analyze/provider.py`; the public default must stay
  plain (CPV-4). No golden reads, screenshots or CV7 / CV13 proof images in the public repo (keep them in atp-private).
- **SEBI wording**: CV12's "area of interest" and "room to the next opposite zone" read close to an entry / target.
  §3 allows the area as a price RANGE only on the user's own symbols. Risks: a single price in a row, "room" as a
  number of points (reads as a target), shared / downloaded PNGs leaving the user's own surface, the level gate's
  R:R (a hidden rule §3 forbids). Defaults: ranges only, room shown as the next zone's range (no points / R:R), no
  strikes, every text through `signals.language` + `wording` + `output_filter`, disclaimer once, no share link
  (download only, disclaimer on the image) (CPV-5, CPV-6). Legal review before public launch stays (COMPLIANCE_IN).
- **Price validation**: zone edges must equal a high / low / open / close of a bar in the table sent (after tick
  rounding) or an engine level; the cited time must be that bar's. Ranges: both edges valid. Exact match only (no
  tolerance) by default; failures dropped and listed "not confirmed" (CPV-7). Digits inside free text are stripped
  (as `vision_audit` does) so a number can only appear via a validated field.
- **Caching**: key = symbol, last closed bar per TF, bar-set fingerprint, data source, checklist version, model, mode.
  Never across organisations: broker-sourced bars are one organisation's data (broker data terms), and a read paid by
  one user's key must not be free for another (CPV-8).
- **Tenancy**: broker bars only through the asking user's own session (BT1b `byob_data_tier`) or the org session;
  the owner's private intraday store (HB2) is never read for others; images / reads stored per user, shown to that
  user only.
- **Look-ahead**: last CLOSED bar only per TF (CV2); the forming bar is cut before rendering and before the table.
- **ADR-0006**: CP-V is read-only, no tool that writes, no proposal / order path touched; tests must assert it.

## CV14: two reading modes

- **Engine readings available today (public)**: `charts/price_action_layer.py` (swings per degree, real breaks,
  Dow trend per TF, protected level, nearest unbroken swing levels, fingerprint) and `price_action/zones/` (zones with
  fresh / tested / broken lifecycle, `get_market_zones`). These cover CV8 items 1-4 and 6 partially; "location" and
  "data gaps" are computable. **Not suitable**: `level_gate` (a confirmation state machine with an R:R minimum and
  trade-side logic - a signal gate, not a reading; showing it would import a rule the user did not set) and the
  chart_analyze private engine (it parses a model's reply; it is not a code reader). The full reader is P5-1 and
  will be private: "Advanced" must call it through a provider boundary (absent = AI only).
- **"Complete reading" default (CPV-10)**: per TF, all of: enough closed bars for the layer's lookback (number in
  settings), last closed bar equals the data's last closed bar, no DH3 quality flag in the window, a trend state, and
  at least one zone above and one below price (else that item is "code: none found", not a failure). Advanced is
  chosen when all four TFs are complete; otherwise AI only, saying which TF was incomplete. The user may switch.
- **Same bars for both**: in Advanced the engine runs in process on the SAME bar set sent to vision (broker or lake),
  not on the lake-only layer endpoint, so "disagree" means a real disagreement (CPV-11).
- **Row source**: `code` / `AI` / `both` (agree within the validation rule) / `disagree` (both shown).

## (d) Open questions

See `docs/OPEN_QUESTIONS.md`, section "CP-V review": CPV-1 .. CPV-14, each with a provisional default.

## (e) PR plan (one `claude/` branch and one draft PR each; no migration unless noted)

1. **CPV-A data**: `chart_read/data.py` - W / D / 1H / 15M closed bars, broker first then lake, per-TF status
   (OK / SHORT / MISSING with counts and dates), no empty-lake answer. Tests: no look-ahead, tenancy, short data text.
2. **CPV-B renderer**: extend `ai/chart_png.py` with a volume pane, clean mode (CV2) and annotated mode (bands,
   swings, break labels, trend arrow, price line); deterministic bytes; golden tests on synthetic bars only.
3. **CPV-C vision + contract**: multi-image `complete_full`; checklist setting (versioned); JSON schema; validator +
   "not confirmed" list + validation log; signal-language filters; resolve / metering reuse. Migration: one table for
   reads (cache, validation log, cost) - after 15:30 IST.
4. **CPV-D limits + admin**: per-user daily reads, remaining count, per-call cost in settings, admin spend line,
   cache per closed bar.
5. **CPV-E UI**: Copilot "read the chart" card (images, row table with source, area of interest, disclaimer, PNG /
   CSV download), phone + desktop, light + dark screenshots, behind `ui_v2` + flag `copilot_chart_read` (off).
6. **CPV-F Advanced (CV14)**: completeness check, code readings from the public layers on the same bars, merge with
   code / AI / both / disagree, manual switch; private-reader hook for P5-1.
7. **Proof (CV7 / CV13)**: run by the owner on the host; screenshots and logs kept in atp-private, not in this repo.

Depends on DATA-H for the lake fallback; CPV-A can ship first with "lake: SHORT / MISSING" stated.
