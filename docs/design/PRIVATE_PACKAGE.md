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
| Test fixtures that are not synthetic: the Trade-derived goldens moved under PR-2, the parity fixtures per layer tag (T13) | Synthetic test cases only (made-up bars, mirror pairs), and a manifest of paths and sha256 for the private fixtures |

Large data stays out of both repositories: real bars go to the market-data lake, Trade's records and the owner's
charts go to the private archive on the host (`docs/design/TRADE_TAKEOVER.md` section 7).

## 3. Layout (proposed)

One Python package, so the host can put it on the backend's import path:

```
<private repo>/
  atp_private/
    reader/           layers 1-6, one owner module per concept (TRADE_TAKEOVER section 3)
    method_pack/      the pack builder, the pack text and its version hash
  knowledge/          the KB, the method, the owner's notes (Markdown, the change-controlled source)
  fixtures/           private test fixtures, same relative layout as ATP's tests expect (ATP_PRIVATE_FIXTURES)
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
   that public CI skips (PR-2) run here.

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
   the fixtures from PR-2 (exported from ATP's history with `scripts/export_private_fixtures.py`, checked against the
   committed manifest).
3. **KB and Method Pack from Trade** (section 8).
4. **Reader layer 1 (T1)** at its stable tag: the reader code into the private package, the provider interface and
   loader into ATP, both in the same week.
5. The later layers, one tag at a time (TRADE_TAKEOVER section 3).

## 8. Moving the KB, the Method Pack and the method docs out of Trade's public tree

Trade's public tree has the KB, the Chart Reader method, the owner's method notes, the generated Method Pack (under
`docs/knowledge/`), the pack builder and its test, and method docs (for example `docs/reports/v22/METHOD_MAP.md`).
Trade's running bot reads the Method Pack (the Vision audit), so the move must not break Trade.

1. **Copy.** A session with access to both repositories copies the files at a pinned Trade commit into the private
   package (`knowledge/`, `atp_private/method_pack/`), with a manifest: Trade path, Trade commit, sha256 per file.
2. **Check.** The owner (or the session) checks that the private copy matches the manifest; the private CI job runs
   the stale-pack test.
3. **Trade reads from the private copy.** Trade gets a read-only deploy key on the DigitalOcean VPS and a setting
   that points the pack builder and the Vision audit at the private checkout. Trade's tests and the bot are checked
   on the VPS.
4. **Only then remove from Trade's public tree** in a normal commit (no history rewrite, no force push), leaving a
   short README that says where the files went (without their content).
5. **Never delete anything before steps 1-3 are done.** If any step fails, Trade keeps its files.

The order for the Trade session is [`TRADE_ORDER_KB_MOVE.md`](TRADE_ORDER_KB_MOVE.md). The owner pastes it into a
Trade session when steps 1-2 are done.

**What stays public:** Trade's and ATP's history keep the old copies (no history rewrite, PR-4). Trade's method code
(the reader being rebuilt) is public in Trade's tree too; that is open question PR-7.
