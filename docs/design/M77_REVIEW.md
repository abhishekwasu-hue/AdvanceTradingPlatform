# M77 review: "Save study" (PDF / PNG / CSV / JSON) against the code (CLAUDE.md §6, before any build)

Order: docs/owner-orders/ATP_ORDER_M77.md (PD-1..PD-5; recorded on `claude/inbox-m71-pl7`). Extends M75 / M76 and
REPLACES M75 PR W-5 (docs/design/M75_REVIEW.md on `claude/m75-review`). Base: `origin/main` f6b795b0. Read by grep +
line ranges; one local measurement of Pillow's PDF output (numbers below). Builds nothing. Rows M77Q-1..4 in
docs/OPEN_QUESTIONS.md.

## 0. Summary

- No study object, no workspace and no image PDF exist today. Nine of the twelve sections depend on M75 / M76 parts that
  are not built (W-3 reading, W-4 card, W-6 plan, W-8 trigger plans, M76 MP-2 / MP-5 / MP-6 / MP-14).
- Building blocks exist: a hand-written PDF writer (`journal/export.py:80-146`, text only, standard fonts, "Rs"), two
  deterministic server PNG renderers (`ai/chart_png.py`, `chart_analyze/reading_png.py`), Pillow 13 (already a
  dependency, `requirements.txt:26`), option-chain analytics, the structure-only share redaction
  (`chart_analyze/share.py:144-179`), the account ZIP (`me/account.py:88-107`), user drawings stored server-side
  (`charts/drawings.py:42-49`, time / price anchors).
- Recommendation: a server-side renderer from a stored study SNAPSHOT (every number computed by the server, never taken
  from the browser); the PDF / PNG / CSV / JSON are views of that snapshot, rebuilt on demand with identical bytes.
- Migration: YES for the build (one new table `copilot_studies`); this review PR: docs only, migration no.

## 1. What exists (export, storage, helpers)

| Need | Exists (file:line) | Gap |
|---|---|---|
| PDF writer | `journal/export.py:80-146` `_Pdf` (A4 landscape, Helvetica / Courier, no image), route `journal/stats_routes.py:67-80` | no image, no embedded font, no rupee sign (`export.py:3-5`), no footer per page |
| Server PNG | `ai/chart_png.py:1-20,127-130` (own PNG encoder, bitmap font, same input = same bytes); `chart_analyze/reading_png.py:1-33` (bands red / green / amber, protected line, break, trigger) | tiny bitmap font; colours are literals (`reading_png.py:20-31`), not from `frontend/src/styles/tokens.css:41-56` (light) |
| Browser PNG | `chartAnalyze/exportImage.ts:1-25` (canvas + tokens + Web Share), `fno/snapshotImage.ts:1-15`, `growth/DisciplineCardV2.tsx:31-37` | `takeScreenshot` (lightweight-charts) used nowhere; DOM overlays would be missing from it |
| CSV | `journal/export.py:39-49` (plain numbers), `screener/ResultBoard.tsx:39-43`, `pages/JournalPage.tsx:160-164`, `exports/service.py` (compliance, SHA-256 header), `tax/routes.py:55` | none for a study |
| Account export (PD-4 bulk) | `me/account.py:1-9,46-49,76-107` ZIP: JSON + CSV per dataset, own rows only (user + tenant), secret columns dropped by name; UI `settings/MyDataCardV2.tsx:58` | studies not a dataset (one tuple entry to add) |
| Journal store (PD-1 "saved to journal") | `db/models.py:461-489` `journal_entries`: one row per TRADE (`trade_id` NOT NULL unique) | a study before / without a trade cannot be a journal entry -> own table; a later trade links it by `plan_json["study_id"]` (no migration there) |
| Private file store | `chart_analyze/storage.py:1-21` (`<root>/<tenant>/<hex>.png`, regex allows `.png` only, `:21`); purged by `retention_days` (`chart_analyze/service.py:400-411`) | volume `private_data` (`docker-compose.yml:146,222,314`) is NOT in the files backup unless the host lists it in `BACKUP_EXTRA_DIRS` (`deploy/atp.sh:1561-1571`); the DB dump is always backed up |
| Share, structure only | `chart_analyze/share.py:1-21` (org members only, HMAC link, version / expiry), `redact` `:179`, digit / name filter `:43,154-158` | reuse for a study's shared copy |
| User drawings | `charts/drawings.py:42-49` (trendline ... `long_position` / `short_position` with stop / target anchors), table `db/models.py:3698-3711` | server renderer for them (anchors are engine-neutral) |
| Indian numbers | backend `journal/export.py:54-77` `indian` / `rupees`; `option_chain/analyses.py:92-120` `_group_indian` / `fmt_number` (two copies); frontend `ui/format.ts:54-148` (`formatNumber`, `formatINR`, locale-free), guard `ui/numberFormatGuard.test.ts` | one backend helper + a parity test against the frontend's cases |
| IST | `journal/stats.py:27` `IST`; frontend `formatDateTimeIST` `ui/format.ts:229` | - |
| Fonts | frontend Inter / JetBrains Mono via `@fontsource-variable` (`main.tsx:5-6`, woff2 only); backend: no font file in the repo; Pillow's built-in Aileron (`load_default(size)`) has NO rupee or em-dash glyph (measured: both draw the missing-glyph box) | bundle TTF files (OFL) with licence text; Pillow has raqm / HarfBuzz (measured), so Devanagari in the user's own notes shapes correctly |
| Versions | `core/config.py:20` `APP_VERSION`; engine `engine_provider/loader.py:111-123` `describe` - "admin System view and health only (never a user surface)" | PD-2.1 "engine tag" vs that rule -> M77Q-2 |
| User name / org | `users` has email only (`db/models.py:71-90`); `tenants.name` | "name on my studies" = a user_prefs value (no migration), off by default |

## 2. Server-side vs client-side rendering: SERVER (decision with reasons)

1. Every number generated by code: the server builds the snapshot from its own reads (bars, zones / engine, chain,
   plan maths, recall). The browser sends only references and the user's own inputs (symbol, timeframes, visible range,
   legs he chose, notes, ticked sections) - never a computed number. A client render would print whatever the page held.
2. Determinism and tests: the repo already proves "same input = same bytes" for server PNGs (`chart_png.py:5-6`).
   Measured here: Pillow PDF output is byte-identical across runs when `creationDate` / `modDate` are fixed. pytest can
   build every section on / off, hash the bytes and keep a fixture PDF as a CI artifact - no browser needed.
3. Same file on phone and desktop (no device pixel ratio, theme or font differences); re-download from "My studies" and
   bulk export work without the page state.
4. No bundle cost: a browser PDF library would be a new frontend dependency against `check-bundle-budget.mjs`.
5. Rejected client option: `takeScreenshot` gives the canvas only (no DOM labels / controls) and differs per screen.
   "Save as image" of one chart therefore uses the same server chart renderer at the user's visible range.

PDF technique (measured, M77Q-1): Pillow's own PDF writer stores RGB pages as JPEG (8 A4 pages at 150 dpi = 1.6 MB,
lossy text) and palette pages uncompressed (34 MB) - neither fits. Recommended: Pillow draws each A4 page (1240 x 1754
px, 150 dpi) and ATP's existing hand-written writer (`journal/export.py`) is extended to embed each page as a
Flate image (PNG data with the PNG predictor; one measured page = 176 KB) plus an invisible ASCII text layer for search /
copy. No new dependency. Alternative: reportlab (BSD, 4.9 MB installed, vector text, subset fonts; no Devanagari
shaping without an extra library).

## 3. Section model (PD-2) - source and "not available" rule

Rule for all: a section is built from snapshot data only; when its source is off, missing, or not built yet, the section
prints its title and one line "Not available: <plain reason>" (e.g. "no option chain for this instrument", "no trade plan
was made"); a missing single value prints "not available", never a blank, a zero or a guess. Each section carries its
own "data as of" time (IST). Every text line passes the advice filter (`ai/output_filter.py:102-127`) except the user's
own notes (verbatim, his words).

| # | Section | Source (today / planned) | Not available when |
|---|---|---|---|
| 1 | Cover | symbol / instrument kind from the instrument store; TFs from the request; LTP from the quotes read with its time; per-source as-of (bars: last closed bar end; zones snapshot `as_of`, `zones_layer.py:3-8`; chain time; engine `as_of`); `APP_VERSION` + reading source (M77Q-2) | LTP has no quote -> "not available" for LTP only |
| 2 | Annotated charts (one per TF) | bars from the lake (closed bars only, `read_chart.py:68-74`); bands / states / swings / BOS / CHoCH / trend / next areas from M75 W-3 reading (engine when `trade_engine`, ATP layers else, labelled); user drawings `charts/drawings.py`; plan lines from section 7. New Pillow chart renderer with the label placer of M75 W-1 (same fixture shapes in Python and TypeScript) | a TF without bars (e.g. option contract intraday, M75 W9) -> that TF's chart box says so |
| 3 | Structure table per TF | `read_chart.structure_part` `:87` (direction, last event + level, protected level); state; nearest areas above / below with distance in points computed from LTP | a field missing in the source (trend per TF and BOS / CHoCH need contract interface 2, M75 W4) |
| 4 | Areas table | ATP: `zones/chart.py:98-105` (band, state, touches, TF) + `compute.py:8-9` row (`formed_at`, `first_touch`, `last_event` - dropped by `candidate`, to carry through); engine: `contract.py:163-178` (no tests / last visit) | tests / last visit for engine areas until interface 2; id shown as a per-study label (S1, B1), never `zone_key` (CLAUDE.md §2) |
| 5 | AI card + "How I worked this out" | M75 W-4 cached hybrid card; tool lines as `copilot/chat/model.ts:206` / `ai/copilot_chat.py:413-427` produce them | AI off, no key / cap (M75Q-5), card not run for this bar |
| 6 | Option context | `option_chain/analyses.py:60` (pcr, max_pain, support_resistance = OI walls, iv_skew ATM IV, expected_move), `dish.py:84-170`; DTE from expiry; chosen strikes' OI / IV / Greeks `option_chain/leg_greeks.py` | not F&O, or no chain (no broker session) |
| 7 | Trade plan | M75 W-6 panel (legs, lots, exits per M68 fields `me/trade_settings.py:166-242`); payoff / max loss / profit / break-evens `options_builder/payoff.py:26-66`; what-if `model.py:100` `pnl_on`; margin broker (`upstox.py:583`, `zerodha.py:390`) else labelled estimate; trigger condition M75 W-8 | no plan made; margin "estimate" label; trigger plan absent |
| 8 | Pre-trade check card | M76 MP-2 result as shown + MP-5 accepted list. The server signs the card result (HMAC as `share.py:54`) so the snapshot never takes a browser-made finding | no ticket was opened / no card |
| 9 | Scenarios | `pnl_on` at BOTH edges of each next area (no midpoint invented) and at his what-if levels (M76 MP-6); margin at a level = estimate only | no plan, or no next area |
| 10 | Similar past setups | `setup_recall/service.py:183-232` needs a subject (trade / proposal, `routes.py:27-39`); a study has neither -> a study context (setup, side, holding, TF from his plan) or M76 MP-14 area context | no plan setup set, or no matches (shows "0 matches", not blank) |
| 11 | Notes / checklist | his free text (verbatim, cap per M77Q-3) + his own `checklist_rules` (`journal/settings.py:66,89`) | nothing written -> "No notes" |
| 12 | Footer every page | fixed text "Information and education only, not advice. Every trading decision is yours." + data sources + "Page n of N"; name / org only when his setting is on | - |

Wording: structure words only; the plan's own fields keep his ticket's labels (his settings, CLAUDE.md §3); state words
per M75Q-4 (no mapping of ATP's four states onto the order's five); the engine's internal invalidation is never shown
(M75Q-3). Colours: a light, print-safe palette generated from `tokens.css` `:root[data-theme="light"]` (`--up` / `--down`
`:55-56`, tints `:309-310`) by a guard test (Python constants = CSS values); his colour-vision setting (`data-cvd`,
`tokens.css:74`) honoured.

## 4. Storage design

- New table `copilot_studies` (migration YES): id, tenant_id, user_id (both indexed, CASCADE), symbol, exchange,
  instrument_kind, timeframes, created_at, title (his own, optional), sections_json (ticked), snapshot_json (schema
  `study/1`), snapshot_sha256, reading_source (`engine` / `atp`), app_version, size_bytes, share_json (version /
  active / expires, as `chart_analyze/share.py:15-20`). Index (user_id, symbol, created_at) for "search by symbol /
  date".
- The PDF / PNG / CSV are NOT stored: rebuilt from the snapshot on download (same bytes; the hash proves it). Reasons:
  the DB is in every backup while the private volume may not be (`atp.sh:1561-1571`); no file-without-row cleanup; the
  bars, drawings and AI card are COPIED into the snapshot because their sources change or expire (drawings are edited,
  chart_analyze items are purged by retention).
- Bounds (admin settings with stated defaults in the PR, not trading numbers): snapshot <= 512 KB, notes <= 5,000
  characters, PDF <= 8 MB, PNG <= 4 MB, phone PNG split into parts above a set height, studies kept until he deletes
  (M77Q-3). Render runs in a worker thread (`asyncio.to_thread`, as `journal/entries.py:244-246`) with a rate limit
  (docs/design/M71_RATE_LIMITS.md).
- Privacy: every read is user + tenant scoped (as `me/account.py:76-85`); never another user's row, email, notes or
  trades; never `broker_account_identifier` (`db/models.py:1196`) even in his own PDF (account label only); email never
  printed. Shared copy = `share.redact` rules (no name, no price, no date, no digit, drawing on a plain background),
  org members only (CA-5 default), explicit tap, stoppable.
- JSON export = the snapshot minus internal ids, engine tokens and settings (M72Q-3); bulk = a `studies` dataset in
  the account ZIP (`me/account.py:46-49`) plus per-study PDFs on request (M77Q-4).

## 5. Gaps and risks

- G1 Nine sections wait on unbuilt M75 / M76 parts; the PDF ships in steps, each later section lights up when its source
  merges (section shows "Not available" until then).
- G2 Contract gaps (trend per TF, BOS / CHoCH, tests, last visit, next areas) are M75 W4 (interface 2), not repeated here.
- G3 Fonts: no TTF in the repo; Aileron lacks the rupee sign. Bundle Inter (the app's font, OFL 1.1) + Noto Sans
  Devanagari (OFL 1.1) for his own notes, with the licence file; size stated in the PR. CI test: raqm present, rupee and
  a Devanagari sample render without the missing-glyph box.
- G4 English-only: all labels English; the user's notes are data. Test fixtures with Devanagari use `\u` escapes
  (`scripts/check_english_only.py`).
- G5 Public repo: the CI sample PDF is built from synthetic fixtures and uploaded as a CI artifact, never committed; the
  owner's real study PDF (his prices, engine areas) is never attached to a public PR or issue - it goes to his private
  report only (`scripts/check_public_repo.py`).
- G6 Two Indian-number helpers in the backend: one shared helper, the old functions kept as thin wrappers (§8, output
  unchanged, tests prove it).
- G7 Same-tab rule (M75): "Save study", "My studies" and share open as sheets / panels; "My studies" also lists in the
  Journal page in place.
- G8 setup_recall has no study subject; adding one must keep "closed strictly before" (no look-ahead, `service.py:18-20`).

## 6. PR plan (after M75 W-2 shell and W-3 annotations; W-5 is replaced by this plan)

Every PR: `ui_v2` + new flag `study_export` (depends on `copilot_workspace`), deploy line + migration yes / no, phone 390
+ desktop screenshots on ONE comparison page per milestone, touched tests locally, full suites in CI.

| PR | Scope | Main files | Migration | Tests (PD-5) |
|---|---|---|---|---|
| SX-1 | Shared Indian number + IST helper; bundled fonts + licence; palette from tokens guard | new `app/study/numfmt.py`, `app/study/fonts/*`, `journal/export.py` (wrappers), `option_chain/analyses.py` (wrappers) | no | parity with `ui/format.ts` cases; journal PDF bytes unchanged; rupee + Devanagari glyph test; palette = tokens.css |
| SX-2 | Snapshot builder + `copilot_studies` + routes (create from references, list / search, get, delete, JSON), account ZIP dataset | new `app/study/{snapshot,store,routes}.py`, `db/models.py`, alembic, `me/account.py:46-49` | **yes** | server ignores client numbers; user B's data never in A's snapshot / JSON (two-user fixture, string scan incl. emails / notes); no account identifier; size bounds refuse with a plain message |
| SX-3 | Page engine + PDF writer (Flate page images, invisible text, footer, page n of N) + sections 1, 3, 4, 11, 12 | new `app/study/{pages,pdf}.py`, `journal/export.py` `_Pdf` extended (kept working) | no | every section on / off (parametrised); missing source -> "Not available"; same snapshot -> same bytes; PDF <= bound; fixture PDF as CI artifact |
| SX-4 | Chart renderer (section 2) + "Save as image" one chart + PNG export (charts, phone single-column) | new `app/study/chart.py` (Pillow), label placer port | no | no label in the axis inset / over another label (shared fixtures with M75 W-1); closed bars only; drawings of every kind drawn |
| SX-5 | Sections 5, 6 (AI card after M75 W-4; option context) + CSV per table (ZIP when several) | `app/study/sections/*`, reuse `option_chain/analyses.py`, `dish.py` | no | AI off -> "Not available"; non-F&O -> "Not available"; CSV plain numbers |
| SX-6 | Sections 7, 9 (after M75 W-6), 8 (after M76 MP-2 / MP-5, signed card), 10 (study context or MP-14) | `app/study/sections/*`, `setup_recall/service.py` (study subject) | no | numbers equal the builder for the same legs; margin labelled estimate where not broker; recall closed-before only |
| SX-7 | UI: "Save study" sheet (format, section ticks all on), "My studies" (workspace + Journal, search), share structure-only copy, bulk export in Settings > Account | `copilot/workspace/SaveStudy*.tsx`, `journal/MyStudies*.tsx`, `settings/MyDataCardV2.tsx` | no | URL path unchanged on every tap; shared copy has no digit / name / date; phone 360 no overflow |
| SX-8 | Proof: one comparison page; a fixture PDF; after deploy the owner saves one real study (attached to his private report, not the public PR) | docs proof page | no | - |

Order: SX-1 -> SX-2 -> SX-3 -> SX-4 (after W-3) -> SX-5 (after W-4) -> SX-6 (after W-6 / MP-2 / MP-5 / MP-6) -> SX-7
(UI can land after SX-4 with later sections "Not available") -> SX-8. None is an order path (read-only; nothing
places, changes or blocks an order).

## 7. Owner decisions (rows in docs/OPEN_QUESTIONS.md)

- M77Q-1 PDF technique: raster pages (no new dependency) vs reportlab vector text.
- M77Q-2 Cover "ATP version / engine tag" vs the rule that engine details are admin-only.
- M77Q-3 Storage: snapshot in the database, files rebuilt on demand; retention and size bounds.
- M77Q-4 Sharing scope and bulk export of PDFs.
