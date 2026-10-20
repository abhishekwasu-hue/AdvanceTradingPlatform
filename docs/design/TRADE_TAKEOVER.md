# Trade takeover: ATP takes over everything from the Trade repo

Status: **plan** (owner decision, 2026-10-20). Nothing is ported yet: on that date Trade (read at `origin/main`
acecd23) has no `docs/reader/STABLE.md`, no `reader-layer*` tags, no `parity/` folder in trade-data, no
`EXPORT_MANIFEST.md` and no `SUNSET.md`. The rows that track the work are section "3a. Trade takeover" of
`docs/TRADE_PORT_MAP.md` (T1-T14).

The same working rules apply as everywhere in ATP: draft PRs, self-review, tests, PAPER only, LIVE untouched, English
only, tenant-safe, settings-driven. Current ATP priorities (load test, UI v2, broker connect) keep their place; the
takeover starts with the data import and the port map, and each reader layer is ported when it becomes stable.

## 1. How the work is split

- **Trade's last work is the Chart Reader.** Trade rebuilds it layer by layer, in the order of Trade's
  `docs/knowledge/CHART_READER_METHOD.md` section 7: (1) swings and structure, (2) areas and lifecycle, (3) zone
  study, commitment and lower-timeframe change, (4) lines and confluence, (5) RSI divergence, (6) the reader output.
- **Each layer the owner approves is tagged stable in Trade:** an entry in `docs/reader/STABLE.md`, a git tag
  `reader-layer<N>-stable-vX`, and parity fixtures in the private trade-data repo under `parity/<layer>/<tag>/`.
- **ATP ports each layer as soon as it is tagged.** It does not wait for all six layers.
- **ATP owns everything after the switch-over** (section 12): the reader, the KB and Method Pack, the trading flow,
  the level bots and the data.

## 2. What ATP needs from Trade, before each step

| Step in ATP | Needs from Trade | Where |
|---|---|---|
| Port reader layer N (T1-T6) | The `STABLE.md` entry for the layer: tag, the module list (owner per concept), the KB / Method Pack version (content hash) it was approved with, what changed since the previous tag | Trade `docs/reader/STABLE.md` |
| | The tag `reader-layer<N>-stable-vX` on the commit that holds that code | Trade git tags |
| | Parity fixtures for that tag: inputs (bars as the layer reads them), expected outputs, mirror cases, and a manifest with a sha256 per file | trade-data `parity/<layer>/<tag>/` |
| Re-port a layer (bug fix) | A new tag, a new `STABLE.md` entry naming the old tag it replaces, and a new `parity/<layer>/<new tag>/` | as above |
| Copy the KB and Method Pack (T7) | The KB, the method, the owner's method notes and the generated pack at each stable tag | Trade `docs/knowledge/` at the tag |
| Data import (T12-T14) | `EXPORT_MANIFEST.md` (and its machine-readable copy): every file to move, with path, sha256, size, kind, symbol and timeframe where relevant, and the ATP destination; the export bundle itself | Trade repo (manifest), trade-data (files) |
| Trading flow (T9, T10) | The owner's dashboard settings for the one bot (symbols, SL / target / lots / exits, approval window) and the MCX bot, as a settings export | part of the export |
| Shadow run (section 11) | Trade keeps running PAPER and exports its signals every day (read-only) so the comparison report can match them | a daily signals file in the export, or a read-only view |
| Switch-over (section 12) | `SUNSET.md`: every service, timer, cron line, webhook and DNS name on the DigitalOcean VPS, the final database export, and the order in which to stop them | Trade `SUNSET.md` |

ATP does not port a layer from an untagged commit, from a branch, or from code without fixtures.

## 3. The per-layer port process

1. **Read the tag.** Fetch Trade at `reader-layer<N>-stable-vX`; read its `STABLE.md` entry; check that
   `parity/<layer>/<tag>/` exists and that every fixture matches its sha256.
2. **Copy the logic as it is, with English names.** One package, `backend/app/reader/`, one owner module per concept,
   as in Trade's structure-reset table (`docs/design/CHART_READER_AGENT.md` section 3) and `docs/reports/v22/METHOD_MAP.md`.
   Trade's Marathi comments and labels are translated; the logic is not changed. Every module header records the Trade
   tag and commit it was ported from; the port map row records the tag.
3. **Parity tests.** The same input gives the same output as Trade's own output in the fixtures, for every case and
   every mirror case. A difference fails the test; nothing is "close enough".
4. **The layer's own tests** move over in English (Trade's tests for the owner modules of that layer).
5. **The CI check grows** (section 4): every number the layer still reads is classified, as in Trade's
   `tests/test_method_no_numbers.py`.
6. **One PR per layer** (draft, self-reviewed), behind the reader flags (section 10). The port map row moves from
   "waiting (no tag)" to "ported" with the tag, the ATP paths and the parity test.

**Parity fixtures stay private.** They are built from licensed market data and never enter ATP's git. They are copied
from trade-data into the private fixtures store on the host (section 7) and the parity tests read them from there
(an environment variable names the store). In the public CI the parity tests are skipped with a visible reason; they
run on the host, or on a private runner that mounts the store read-only. A fixture manifest (paths and sha256 only,
no data) may be committed so a test can prove it read exactly the tag's fixtures. Each layer also keeps a few small
synthetic cases in git (made-up bars, mirror pairs) so the public CI always exercises the code.

## 4. Rules carried over from Trade

- **No numbers and no time logic in reading.** Reading compares (larger than, beyond, inside); it never uses fixed
  thresholds, ratios, counts, widths, multipliers, clock times, day or session resets. Numbers belong only to
  operational dashboard settings (lots, SL, target, exits, schedules), display limits and data-quality checks
  (METHOD sections 1 and 6).
- **The CI check.** `backend/tests/test_reader_no_numbers.py` mirrors Trade's `tests/test_method_no_numbers.py`:
  every numeric setting of the reader must be classified as a legacy what-if (off by default), operational, display
  or data quality; an unclassified number fails the build. It starts with layer 1 and grows with each layer; at
  layer 6 nothing may be "pending".
- **Futures volume for indices.** The index has no volume; volume comes from the near-month future (the contract
  expiring on or after the day; two contracts are never summed). Missing futures volume is a data problem: report it,
  no entry.
- **Spot for structure, areas and RSI.** Swings, areas, lines and RSI are read on the spot chart.
- Also carried over: closed bars only (a running bar is never read); every price in the output exists in the data;
  no R:R gates, no breakout entries, no wave-end prediction; trend side and pullback entries only; mirror cases for
  both sides; Trade's sealed holdout window stays sealed (ATP already has `backend/app/backtest/data_policy.py`).

## 5. Tenant safety

- Every read runs on the tenant's own broker data (or data the tenant is entitled to), never another tenant's.
- No shared mutable state in `backend/app/reader/`: no module-level caches keyed without the tenant, no globals that
  hold bars, areas or working memory. Caches, if any, are keyed by tenant and input hash and hold pure results.
- The owner's licensed intraday history (HB0 / HB2 private store) is read only for the owner's tenant.
- Reader outputs, Vision audits and approvals are stored per tenant and per user, like every other ATP record.

## 6. A new Trade tag

A new version tag for a layer (a bug fix in Trade) means: re-port the changed modules, re-run parity against the new
`parity/<layer>/<new tag>/`, update the module headers and the port map row. Later layers that depend on it re-run
their parity too. Until the re-port is merged, ATP keeps the previous tag (never a mix of two tags in one layer).

## 7. Data import

Everything in Trade's `EXPORT_MANIFEST.md` is imported on the Hostinger host, never into git:

- **Bars** go to the market-data lake: daily bars to the shared lake (`md_candles`; NSE daily is already there from
  the HB1 bhavcopy backfill, so Trade's daily files are a cross-check), intraday index and futures bars to the owner's
  private store (`md_tenant_candles`, HB0 / HB2 rules: one confirmed owner tenant, never the shared lake). The existing
  HB0 importer (`app.market_lake.r2_import`) does the checks and the writes; the import converts Trade's files into
  its layout first.
- **Golden fixtures, parity fixtures and the owner's approved charts** go to a private fixtures store on the host, used
  by tests only. They are never part of the Method Pack.
- **Trade's records and review artefacts** (PAPER journal, signals, Vision log, review and audit charts) go to a
  private read-only archive on the host.
- **Checksums:** every file is checked against the manifest's sha256 before anything is written; one mismatch stops the
  run.
- **Backups:** lake rows are in Postgres and covered by the database backups; the fixtures store is added to the
  encrypted files archive (`BACKUP_EXTRA_DIRS`); the archive is copied to R2 once the owner adds R2.
- **The command:** `atp.sh import-trade`, run by the owner on the host, with a dry run, idempotent, and never touching
  flags, secrets or other tenants. Its design (manifest format, destinations, steps, what the owner does) is a
  separate design note that comes with its PR.

## 8. KB and Method Pack

- ATP copies the KB, the method and the generated Method Pack at the version that matches each stable tag (the pack
  version is its content hash, recorded in `STABLE.md` and in every reader and Vision log line).
- The pack is generated from the KB, never edited by hand; a stale-pack test fails when they differ (as in Trade).
- After the switch-over ATP owns them with the same change control: **KB diff -> the owner approves -> code -> test**
  (plus the traceability row). No rule changes in code without a KB change first; no fixes fitted to one day.

## 9. The trading flow for the owner's tenant

The Trade bot is rebuilt as ATP features, PAPER only:

- **The 15M SRv2 engine-signal flow becomes an ATP deployment type** (a `strategy_deployments` row whose strategy is
  the reader flow). Signals come from the ported reader on each closed 15-minute bar.
- **Telegram approval:** ✅ approve, ✏️ change the entry only (SL and target stay the engine's; the changed ticket needs
  a new ✅), ❌ reject with a reason. Entries go through the E1 execution core and `app.execution.approval`; the AI
  never places an order.
- **PAPER execution;** SL / target / lots / exits come from the dashboard settings, never from the reader.
- **Exits are never blocked** (ADR-0004): no approval, budget, flag or Vision result can stop an exit.
- **Vision audit before approval:** audit-only (it never blocks, sizes or changes a decision), with the Method Pack
  checklist, on the BYOK key and budget rules ATP already has (`app.ai.vision_audit`).
- **The MCX bot** is ported as its own deployment type on the same core, PAPER only.
- **The level bots** are rebuilt in ATP on the ported reader after its last layer is stable; they are not rebuilt in
  Trade.

## 10. UI

- **The Charts page** gets a **"Read this chart"** panel: the reader output (the story, the areas and lines, the
  current state, the evidence) shown as the situation and the possibilities, never advice.
- **The "Reader zones & lines" overlay** draws the reader's own areas and lines.
- **Both sit behind flags (off) until the reader is complete:** one for the panel and one for the overlay (proposed
  names `chart_reader_panel` and `chart_reader_overlay`; the Charts plan's `reader_overlay` is the user's on / off
  preference inside the chart, shown only while the flag is on).
- **The output contract** is Trade's `docs/knowledge/CHART_READER_METHOD.md` section 5: the story; the verdict (enter /
  wait / no trade) with the reason in plain words; the evidence list (present / absent), no scores (A = the required
  set plus at least one confirming piece, B = the required set only); every price exists in the data.

**Change to the Charts page plan.** In the Charts page plan (UI2-4b), open question CHP-5 proposed that the "Reader
zones & lines" overlay show market zones (areas) plus price-action levels and trendlines (lines) as a stand-in, because
the Trade Chart Reader was not ported. With the takeover, the overlay means **the ported reader's output** (layer 2
areas, layer 4 lines and confluence, from layer 6's output), behind its own flag. Market zones and the price-action
layer stay separate chart layers under their existing flags (`market_zones`, `chart_price_action`); they are not the
reader overlay. Until the reader's last layer is stable the overlay stays hidden.

## 11. Shadow run

- When the reader and the flow above are ported, they run for the owner's tenant in PAPER on live data, side by side
  with Trade, until the owner decides.
- Every Telegram message from ATP in the shadow is labelled **SHADOW**.
- **A daily comparison report:** every Trade signal against every ATP signal for the day: same or different, and the
  reason for each difference (a different area, a different visit, missing futures volume, a data difference, and so
  on). It needs Trade's daily signals export (section 2).

## 12. Switch-over

- The switch-over is the owner's decision.
- After it, Trade becomes a read-only archive (the repo stays; nothing more is merged).
- The DigitalOcean VPS is switched off by the owner with **one command block** that ATP prepares from Trade's
  `SUNSET.md`: stop and disable every Trade service and timer, remove its cron lines, take the final database export
  (and check it), stop the webhook tunnel, then power off. ATP prepares it only when `SUNSET.md` exists, and the
  owner runs it; nothing in ATP reaches the VPS.
