# The private package: Chart Reader, KB and Method Pack outside the public repository

Status: **plan** (owner decision PR-1, 2026-10-20; the reader port plan of production readiness B9 added 23 Oct,
sections 9-14). The private repository does not exist yet: the owner creates it (section 6), then it is filled in the
order of section 7. What exists in ATP's public code today is the boundary only: the Analyze-a-chart engine loader
(`backend/app/chart_analyze/provider.py`, module `atp_private.chart_analyze`), the private fixtures resolver
(`backend/tests/private_fixtures.py`, `ATP_PRIVATE_FIXTURES`), its manifest and export script, and the guard's name
refusals. The reader provider, its loader and the Method Pack loader are not built (section 9 is their plan).

## 1. The decision

ATP stays public. The Chart Reader, the KB and the Method Pack live in a **separate private repository** (the "private
package"), installed on the host and tested by a private CI job. Nothing from the reader or the KB is committed to
ATP's public repository; `scripts/check_public_repo.py` refuses the KB and Method Pack by name, and review catches the
rest. The rules of [`docs/PUBLIC_REPO_POLICY.md`](../PUBLIC_REPO_POLICY.md) apply to everything ATP commits.

Trade's repository is public too, and its tree carries the KB, the Method Pack and the method docs today. They move
into the same private package as part of the Trade takeover (section 8); Trade removes them from its public tree only
after the private package holds them.

## 2. What goes where

| Private package (private repository) | ATP public repository |
|---|---|
| The ported reader, layers 1-6 (T1-T6 of `docs/TRADE_PORT_MAP.md`) and its own tests, including the "no numbers in reading" check | The boundary: a small provider interface and a loader that finds the private package if it is installed (section 4) |
| The KB, the Chart Reader method, the owner's method notes, the generated Method Pack, the pack builder and its stale-pack test (T7) | The deployment type, the approval flow, Telegram, PAPER fills, exits (never blocked, ADR-0004), the Vision audit plumbing (BYOK, budget), all generic |
| The Vision checklist text built from the Method Pack (the method part of T8) | The "Read this chart" panel and the overlay, behind their flags (off), rendering whatever the provider returns |
| The method-specific rules of the level bots rebuilt on the reader (T11) | The generic bot and deployment code those rules plug into |
| The Trade-derived goldens moved under PR-2 (Trade's outputs on synthetic inputs, no licensed data) | Synthetic test cases only (made-up bars, mirror pairs), and a manifest of paths and sha256 for the private fixtures |

Licensed data and large data stay out of both repositories, GitHub-hosted or not: real bars go to the market-data
lake; the per-layer **parity fixtures** (built from licensed market data, T13), Trade's records and the owner's charts
go to the private fixtures store and the private archive on the host (`docs/design/TRADE_TAKEOVER.md` sections 3
and 7). Putting licensed-data fixtures into the private repository would need an owner decision first.

## 3. Layout (proposed)

One Python package, so the host can put it on the backend's import path:

```
<private repo>/
  atp_private/
    reader/           layers 1-6, one owner module per concept (TRADE_TAKEOVER section 3)
    method_pack/      the pack builder and its version hash (the pack is built from knowledge/)
  knowledge/          Trade's docs/knowledge/ as is: the KB, the method, the owner's notes, the generated pack
  fixtures/           the PR-2 goldens, same relative layout as ATP's tests expect (ATP_PRIVATE_FIXTURES)
  tests/              the reader's tests, parity tests, the no-numbers check, the stale-pack test
  .github/workflows/  the private CI job (section 5)
  ATP_VERSION         the ATP commit or tag this version is tested against
```

The repository is named **`atp-private`** (production readiness B9, 23 Oct); the Python package inside it is `atp_private`.

## 4. The boundary in ATP

- **A provider interface** in ATP's public code describes what the reader returns, in generic terms: the setup state
  (formed / forming / none), the structure events and areas, an evidence list (present / absent), the shapes for the
  chart overlay, the reader version and the Method Pack version hash. Since the signal-language decision (handover
  11l) everything shown is expressed in ATP's public signal-language tokens, never in free text from the package; the
  exact types are in section 9.
- **A loader** imports `atp_private` if it is installed. If it is missing, the reader features say "not installed",
  their flags stay off, and nothing crashes; the rest of ATP is unaffected. The loader logs the package version and
  the pack hash, never its content.
- **Public CI** tests the boundary with a synthetic fake provider. It never installs the private package, never holds
  a key for it, and never prints anything from it.
- **Tenant safety** (TRADE_TAKEOVER section 5) applies inside the private package too: no shared mutable state, caches
  keyed by tenant and input hash.

The interface and the loader come with the first reader PR (T1), not before. Their plan is sections 9 and 10.

## 5. The private CI job

The private CI job runs **in the private repository's own GitHub Actions**, so its logs are private:

1. Check out the private repository (its own checkout needs no extra key).
2. Check out ATP's public repository at the commit in `ATP_VERSION` (public, no key needed).
3. Install ATP's backend requirements and the private package.
4. Run the private tests, then ATP's backend tests with `ATP_PRIVATE_FIXTURES` pointing at `fixtures/`, so the tests
   that public CI skips (PR-2) run here. The parity tests on licensed data run on the host only, where the private
   fixtures store is.

**No key in CI.** The owner's decision names "a private CI job with a read-only deploy key". Running the job inside the
private repository needs no key at all (it checks out its own repository and the public ATP), which is safer; the
read-only deploy keys are for the machines that fetch the private repository: the Hostinger host (section 6) and,
during the Trade takeover, Trade's VPS (section 8).

The job does not run in ATP's public workflows. Their logs are public. Repository secrets are not given to pull
requests from forks. And a failing private test could print private data.

**Minutes:** private repositories use the account's included Actions minutes (the public ATP repository does not).
Keep the job small: run it on push to the private repository's main branch and on a weekly schedule, with the
spending limit the owner set.

## 6. What the owner does (once)

1. **Create the private repository** on the same GitHub account as ATP, empty, visibility **Private**.
2. **Add a read-only deploy key for the host.** On the Hostinger host, as the `atp` user:
   `ssh-keygen -t ed25519 -f ~/.ssh/atp_private_ro -N "" -C "atp host read-only"`. In the private repository:
   Settings -> Deploy keys -> Add deploy key, paste the content of `~/.ssh/atp_private_ro.pub`, and leave
   **"Allow write access" unticked**. The private key never leaves the host and is never pasted into a chat.
3. **Give the Claude sessions access** to the private repository (Claude GitHub app, repository access), so a
   session can scaffold it and open pull requests there.
4. **Protect `main` of `atp-private`** (Settings -> Branches): pull requests only, the private CI job (section 5)
   required, no force push. Set an Actions spending limit for private minutes (Settings -> Billing); 0 is fine until the
   first private CI run.
5. **Trade's VPS key, only for the KB move** (section 8): a second read-only deploy key made on the VPS the same way
   (`-C "trade vps read-only"`), added to `atp-private` with write access unticked. Remove it when the switch-over is
   done.
6. Later, when the first reader layer is ported: the host install step (below) is added to `atp.sh` by a PR, and the
   owner runs it with his one consolidated deploy command (no separate step on the server).

Never paste a private key, a token or a deploy-key file into a chat, an issue or a pull request; only the `.pub`
half goes into GitHub's Deploy keys page. The owner's list in plain words is section 14.

**The host install** (designed with the T1 PR): the host keeps a checkout at `/opt/atp/private`, fetched with the
deploy key at the version ATP's `.env` names (`ATP_PRIVATE_REF`, a tag of the private repository), mounted read-only
into the backend container and put on its import path. Images built by public CI never contain private code.

## 7. Order

1. The owner creates the repository and the deploy key (section 6).
2. **Scaffold** (a session with access to the private repository): the layout of section 3, the private CI job, and
   the goldens from PR-2 (exported from ATP's history with `scripts/export_private_fixtures.py`, checked against the
   committed manifest).
3. **KB and Method Pack from Trade** (section 8).
4. **Reader layer 1 (T1)** at its stable tag: the reader code into the private package, the provider interface and
   loader into ATP, both in the same week.
5. The later layers, one tag at a time (TRADE_TAKEOVER section 3).

## 8. Moving the KB, the Method Pack and the method docs out of Trade's public tree

Trade's public tree has the KB, the Chart Reader method, the owner's method notes and the generated Method Pack (under
`docs/knowledge/`), and method docs (for example `docs/reports/v22/METHOD_MAP.md`). Trade's running PAPER bot reads
the Method Pack (the Vision audit), so the move must never break it.

1. **Copy.** A session with access to both repositories copies the files from Trade's `main` at a pinned commit into
   the private package (`docs/knowledge/` as is, generated pack included, into `knowledge/`, the directory Trade's
   setting names; a copy of the pack builder into `atp_private/method_pack/`), with a manifest:
   Trade path, Trade commit, sha256 per file.
2. **Trade switches to the private copy, then removes its own.** The order for the Trade session is
   [`TRADE_ORDER_KB_MOVE.md`](TRADE_ORDER_KB_MOVE.md); Abhi pastes it into a Trade session after step 1. In short: check
   Trade's current `main` and the private copy against the manifest; add a setting that points Trade's pack builder and
   Vision audit at a private checkout (tests skip visibly without it); Abhi clones the private repository on the VPS
   at a pinned tag with a read-only deploy key, sets the setting for the running services, restarts them and shows one
   real Vision audit loading the pack hash from the private copy; **only after Abhi confirms that** does Trade remove
   the files from its public tree (a normal commit), make the setting required (a missing path fails loudly) and add a
   CI check that keeps them out. The pack builder code stays in Trade, pointed at the private copy.
3. **Never delete anything before step 2's confirmation.** If any step fails, Trade keeps its files.

**After the move (change control):** the KB, the method and the Method Pack are edited only in the private repository
(KB diff -> the owner approves -> code -> test). Trade's `STABLE.md` records, per stable tag, the private repository's
commit and the pack hash; ATP's ports read the KB and the method docs from the private repository at that commit. The
VPS and the host each pin a private tag; nothing follows a moving branch.

**Trade becomes private (owner decision PR-7, 2026-10-20).** Once Trade's repository is private, this move is no
longer needed to keep the KB out of public view; it still gives the KB, the method and the Method Pack one source of
truth in the private package, and the owner decides when to run it. The safety order above applies unchanged when it
runs.

**What stays public:** Trade's and ATP's history up to the change of visibility keeps the old copies (no history
rewrite, PR-4).

## 9. The reader interface (B9 plan)

Production readiness B9 (23 Oct) plans the port of Trade's "decision3" reader (T1-T6 of `docs/TRADE_PORT_MAP.md`)
into `atp-private`. This section is the **interface only**: what goes in, what comes out, in the types ATP already
uses. It holds no reading rule, no method, no number of the method and nothing from the KB; those stay in
`atp-private`. Nothing here is built yet; it lands with the T1 PR (section 7 step 4).

### 9.1 Where it sits in ATP

- **Public module (planned):** `backend/app/reader/provider.py`, the same shape as the Analyze-a-chart boundary that
  exists (`backend/app/chart_analyze/provider.py`): a `ReaderProvider` protocol, a `load()` that imports
  `atp_private.reader` and calls its `get_provider()`, `describe()`, and `set_for_tests()` / `reset_for_tests()` for a
  synthetic fake. The two loaders may later share one helper (`app/platform/private_package.py`); that refactor keeps
  the Analyze-a-chart behaviour byte-identical.
- **The strategy adapter (planned):** `backend/app/reader/adapter.py`, a `BaseStrategy` that reads the provider's
  series at the decision bar, built like the strategy-template adapter (`app/strategy_templates/adapter.py`): no
  `build_signal` (no targets or R:R the user never set), the invalidation as the stop reference, targets and exits
  only from the user's deployment settings (`app/strategy_templates/exits.py`), the provenance on `Signal.provenance`
  (never shown). A strategy id `reader:<reader version>:<settings hash>` resolves to it in
  `app/custom_strategies/resolver.py`, behind the reader flags (section 10).
- **Only `provider.py` names `atp_private`.** A public test checks it, as `test_no_method_text_in_the_public_package`
  does for Analyze a chart.

### 9.2 Inputs

```
ReadRequest
  candles:   Mapping[timeframe, Bars]    closed bars only, oldest first, one frame per timeframe the settings name
                                         (the signal timeframe plus its context timeframes); a running bar is never
                                         passed. Bars = time (UTC, tz-aware), open, high, low, close, volume.
  volume:    Optional[Mapping[timeframe, Bars]]
                                         for an index: the near-month future's volume on the same bar times
                                         (TRADE_TAKEOVER section 4); missing = a data problem the reader reports
  settings:  ReaderSettings              the user's own settings for his adopted reader strategy: a frozen mapping of
                                         name -> value, validated against the schema the package publishes
                                         (`settings_schema()`), every key classified as operational / display /
                                         data quality / legacy what-if (TRADE_TAKEOVER section 4)
  symbol:    str                         the instrument key; the package never fetches data itself
  as_of:     datetime                    the close time of the last closed bar of the signal timeframe
```

- ATP fetches the bars (the tenant's own data or data it is entitled to; TRADE_TAKEOVER section 5) and hands them in.
  The package does no I/O: no network, no database, no file read outside its own installed files.
- How many bars to fetch comes from the package (`history_bars(settings) -> Mapping[timeframe, int]`), times ATP's
  safety factor, as R6 does for templates (`app/strategy_templates/parity.py`, `fetch_bars`).

### 9.3 Outputs

```
ReadResult
  version:         str                   reader version (section 11)
  method_version:  str                   the Method Pack content hash the reader was built with
  interface:       int                   the interface version this result follows (section 11)
  state:           "formed" | "forming" | "none"
  direction:       +1 | -1 | 0           internal: the side the setup reads; never shown as an instruction
  invalidation:    Optional[float]       internal: where the reading is proven wrong; used as the stop reference only
                                         when the user's exits say so; never in signal text
  structure:       SignalStructure       the shown reading, as token ids (app/signals/language.py)
  events:          tuple[StructureEvent, ...]
  areas:           tuple[Area, ...]
  lines:           tuple[Line, ...]
  swings:          tuple[Swing, ...]
  data_problems:   tuple[str, ...]       token ids (missing futures volume, a gap in the bars, ...); any one = no setup
  instance:        Optional[str]         a stable id of the setup, so scan / backtest / live can be compared
```

The types reuse what ATP already has:

| Part | ATP type it maps to | What it carries |
|---|---|---|
| `structure` | `SignalStructure` (`app/signals/language.py`): `trend`, `side`, `steps`, `setup`, `evidence`, `timeframe`, `area_range` | Token ids only. `format_signal` turns them into the shown sentence; `unknown()` refuses any token without a public phrase, so the package can never bring its own text to a screen. |
| `StructureEvent` | a `STEPS` / `SETUPS` token plus the bar time and timeframe it happened on | "the pullback reached the seller area", "a handover is forming inside the area", in the order they happened. |
| `Area` | the Analyze-a-chart contract's area (`app/chart_analyze/contract.py`): side `buyer` / `seller`, state `fresh` / `tested` / `broken` / `flipped`, low and high, the timeframe, the bar it was born on | The area's price range may be shown on the user's OWN signals only ("Seller area: 23,000–23,100", `area_label`); never on a public surface. |
| `Line` | the same contract's line: two or more touch points (time, price), its state | Drawn on the chart overlay only. |
| `Swing` | `HH` / `HL` / `LH` / `LL` at a bar time and price | Drawn on the chart overlay only. |
| evidence | `EVIDENCE` tokens, present / absent, never a score | Listed, not weighted. |

Rules the boundary enforces, whatever the package returns:

- **Signal language.** The shown text comes only from `format_signal` / `check` on the tokens; a result whose
  `structure.unknown()` is not empty is refused (the signal is not shown, the worker logs the token ids, never the
  method). No buy / sell / target / stop-loss wording, no single price; the area range only on the user's own
  signals. A new token the reader needs is a public, generic structure phrase added to `app/signals/language.py`
  and its frontend mirror in a reviewed public PR (the shared cases in `tests/fixtures/signal_language.json` grow
  with it); the meaning of when the reader emits it stays private.
- **Prices exist in the data.** Every area edge, line touch and swing price must be a price in the bars handed in;
  anything else is dropped (the contract's bounds, as for Analyze a chart).
- **No order field.** The result has no lot, quantity, order type or target; the user's deployment settings decide
  those (CLAUDE.md "Trading logic"). The AI never places, blocks or changes an order (ADR-0006).
- **Pure and deterministic.** The same request gives the same result, byte for byte; a read at bar t uses only bars
  up to t; no module-level state keyed without the tenant (TRADE_TAKEOVER section 5).
- **A broken package never breaks ATP.** An exception in `read` is caught, logged with its class name only, and the
  bar reads as "no setup".

`series(request) -> tuple[ReadResult, ...]` (one result per closed bar of the signal timeframe in the window) is the
second entry point, so a backtest computes the series once instead of once per bar, as the template adapter does.
`series(...)[-1]` must equal `read(...)` on the same bars (a parity test, section 12).

## 10. How ATP loads it, and the fallback

- **Optional install.** `atp-private` is not a dependency of ATP: not in `requirements.txt`, not in any image built
  by public CI. On the host it is a read-only checkout at `/opt/atp/private`, fetched with the host's read-only deploy
  key at the tag `ATP_PRIVATE_REF` in `/opt/atp/.env`, mounted read-only into the backend and worker containers and
  put on the import path (section 6, "The host install"; the `atp.sh` step comes with the T1 PR). A developer box
  without access simply does not have it.
- **The loader** (section 9.1) imports `atp_private.reader` once per process. It refuses, and logs why in one line
  (name, version, interface number only): a missing module, a missing `get_provider`, a provider that does not
  satisfy the protocol, or an `interface` number ATP does not support. Refused = "not installed".
- **When it is absent (or refused):**
  - users see the **generic strategy templates** (`app/strategy_templates`, the Idea Library and Paper bot pre-fill)
    as today; nothing on a user page mentions a missing package;
  - the reader flags (`chart_reader_panel`, `chart_reader_overlay`, and a deployment flag for the reader strategy,
    all registered OFF in `app/platform/flag_catalog.py` with the T1 PR) cannot be switched on: the admin console
    shows "Reader not installed" next to them;
  - an existing deployment whose strategy is `reader:...` opens **no new entries** and says "This strategy is paused:
    its engine is not available" on the Paper bot page; its **open positions keep their exits** (the position monitor
    does not depend on the strategy; ADR-0004);
  - the Vision audit runs without the method checklist (it is audit-only and never blocks anything).
- **Admin "view".** Admin > System shows the installed reader version, the method hash and the interface number, or
  "not installed". Never any of its text.

## 11. Versioning

- **The reader version** is the `atp-private` git tag, `reader-vMAJOR.MINOR.PATCH`. Its notes record, per layer, the
  Trade tag it was ported from (`reader-layer<N>-stable-vX`, TRADE_TAKEOVER section 3) and the Method Pack hash.
  MAJOR = a reading change (signals can differ); MINOR = an added output or setting with old outputs byte-identical;
  PATCH = no output change.
- **The interface number** is an integer in ATP's public `app/reader/provider.py` (`INTERFACE_VERSION`). The package
  states the numbers it implements; the loader accepts only a match. An interface change is a public PR that keeps
  the previous number working until the package follows.
- **`ATP_VERSION`** in `atp-private` names the ATP commit or tag the private CI tested against; `ATP_PRIVATE_REF` in
  the host's `.env` names the private tag the host runs. The host never follows a branch.
- **Provenance.** Every reader signal carries the reader version, the method hash, the settings hash and the setup
  instance on `Signal.provenance` (never shown, never serialised to a user), and the worker logs them.
- **A new version for users.** A MAJOR version is not swapped under a running deployment silently: the release note
  in the admin console lists it; the owner runs the parity checks and a PAPER shadow (TRADE_TAKEOVER section 11)
  before the host's `ATP_PRIVATE_REF` moves, and a deployment records the reader version it was started with.

## 12. Parity tests: scan = backtest = live

Three kinds, all of them on the reader's SIGNAL SERIES (not trades), as `app/strategy_templates/parity.py` does for
templates:

1. **Port parity (Trade = ATP).** Per layer, the ported code gives exactly Trade's output for every fixture and every
   mirror case at the layer's stable tag. Nothing is "close enough".
2. **Path parity (scan = backtest = live).** On one fixed trailing window of closed bars, the same series from: a
   scan of the reader strategy, `run_backtest`, `run_option_backtest` (the user's spread construction on top), and
   the deployment worker's call bar by bar. Plus: `series(...)[-1] == read(...)`, truncation (a read at t on bars up
   to t equals the series at t; no look-ahead), determinism (two runs byte-identical), mirror cases on both sides,
   and the history the worker fetches (`history_bars` times the safety factor) is enough for the first decision bar.
   The public harness is generalised so the reader adapter plugs into it the same way the template adapter does.
3. **Shadow parity (ATP live = Trade live).** The daily comparison report of TRADE_TAKEOVER section 11, for the
   owner's tenant, while both run PAPER.

**Where the fixtures live: never here.**

| Fixture | Where | Run by |
|---|---|---|
| Trade's outputs on synthetic inputs (the PR-2 goldens and the reader's own) | `atp-private` `fixtures/`, same relative paths, a manifest with sha256 | the private CI job |
| Parity fixtures built from licensed market data (`parity/<layer>/<tag>/`) | the private fixtures store on the host only, never any git repository (section 2) | the host, `ATP_PRIVATE_FIXTURES=<store> python -m pytest ...` |
| Small synthetic cases (made-up bars, mirror pairs) for the boundary | ATP's public tests, pinned in `ALLOWED_TEST_DATA` | public CI, with the fake provider |

The public tree holds at most a manifest of paths and sha256 (no data), as `backend/tests/private_fixtures_manifest.json`
does today, and the guard refuses any listed file or its hash coming back (`docs/PUBLIC_REPO_POLICY.md` section 5).

## 13. CI: the private job, and the public CI without the package

**`atp-private` CI** (its own GitHub Actions, private logs; section 5 is the job's base):

- On every pull request and push to `main`, and weekly: the package's own tests, the no-numbers check
  (`tests/test_reader_no_numbers.py`), the stale-pack test, the port parity on `fixtures/`, then ATP's backend tests at
  `ATP_VERSION` with the package installed and `ATP_PRIVATE_FIXTURES=fixtures/`, so the private-fixture tests and the
  path parity with the real reader run.
- A **compatibility job** (weekly and on demand): the same ATP tests at ATP's current `main`, so a boundary change in
  ATP is seen before the next release. A failure there opens nothing in the public repository.
- A **release job** on a `reader-v*` tag: the full suite at `ATP_VERSION` must be green; it writes the release notes
  (layer tags, method hash, interface number). No artefact leaves the repository; the host fetches the tag.
- A **leak check** in the other direction is not needed there, but the job never prints fixture content or method
  text in its logs (assertions report a case id and a field name, not values).

**Public ATP CI** stays green without it, and never sees it:

- It never installs `atp-private`, holds no key or token for it, and never prints anything from it.
- `load()` returns None there; a public test asserts that, and the boundary tests run on a synthetic fake provider
  (a made-up reading on synthetic bars) through the adapter, the path-parity harness, the signal-language check and
  the "not installed" fallback (section 10).
- Tests that need the private fixtures skip with the visible reason `private fixture store not mounted` (pytest
  `-rs`), exactly as today; a set but empty store is an error, never a skip.
- `scripts/check_public_repo.py` keeps refusing the KB / Method Pack names and the manifest's files; a public test
  checks that only the provider module names `atp_private`.

## 14. When, and what the owner does

**When.** The port starts **after Trade's PAPER results are stable, and Abhi decides the day.** Nothing is ported
from an untagged commit. The provisional checklist ATP proposes to him (open question PRV-3): each layer has its
`STABLE.md` entry, its `reader-layer<N>-stable-vX` tag and its parity fixtures; Trade's PAPER bot has run on that
version without a reading change for a period Abhi names; Abhi says "start". Until then ATP builds nothing of the
reader, and users keep the generic templates.

**Abhi's steps** (section 6 has the clicks):

1. Create the private repository **`atp-private`** on the same GitHub account, empty, visibility Private.
2. On the Hostinger host, as `atp`, make the read-only deploy key and add its `.pub` half to `atp-private` (Deploy
   keys, write access unticked). The private key stays on the host.
3. Give the Claude GitHub app access to `atp-private`, so a session can scaffold it (section 7 step 2).
4. Protect `atp-private` `main` and set the private Actions spending limit.
5. Only if the KB move of section 8 runs: a second read-only deploy key from Trade's VPS.
6. When the first layer is ported and merged: put the private tag in `/opt/atp/.env` as `ATP_PRIVATE_REF` (the
   session gives the exact line) and run the one consolidated deploy command. Nothing else on the server.
