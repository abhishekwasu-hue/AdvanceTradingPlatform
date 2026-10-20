# The private package: Chart Reader, KB and Method Pack outside the public repository

Status: **plan** (owner decision PR-1, 2026-10-20). Nothing exists yet: the private repository is created by the owner
(section 6), then filled in the order of section 7.

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

The repository name is the owner's choice (a neutral name, for example `atp-private`).

## 4. The boundary in ATP

- **A provider interface** in ATP's public code describes what the reader returns, in generic terms: a decision
  (enter / wait / no trade), the reason in plain words, a story, an evidence list (name, present / absent), the price
  levels and the shapes for the chart overlay, the reader version and the Method Pack version hash. The evidence names
  and their meaning come from the private package at run time; the public code only renders them.
- **A loader** imports `atp_private` if it is installed. If it is missing, the reader features say "not installed",
  their flags stay off, and nothing crashes; the rest of ATP is unaffected. The loader logs the package version and
  the pack hash, never its content.
- **Public CI** tests the boundary with a synthetic fake provider. It never installs the private package, never holds
  a key for it, and never prints anything from it.
- **Tenant safety** (TRADE_TAKEOVER section 5) applies inside the private package too: no shared mutable state, caches
  keyed by tenant and input hash.

The interface and the loader come with the first reader PR (T1), not before.

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
4. Later, when the first reader layer is ported: the host install step (below) is added to `atp.sh` by a PR, and the
   owner runs it.

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
