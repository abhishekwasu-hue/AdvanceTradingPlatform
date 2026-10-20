# Order for a Trade session: move the KB, the Method Pack and the method docs out of Trade's public tree

Abhi pastes this into a Trade session **after** the private package holds a copy of the files with its manifest
(step 1 of section 8 of `docs/design/PRIVATE_PACKAGE.md` in ATP). It is not to be run before. Before pasting, Abhi
fills in the private repository's name and attaches that repository to the Trade session (read access), so the
session can read the manifest.

---

From Abhi, for the Trade repository (English).

Owner decision PR-1 (20 Oct): the Chart Reader, the KB and the Method Pack live in a separate **private** repository
(the "private package": `<private repository name>`). The Trade repository is public, so its KB, Method Pack and
method docs leave Trade's public tree. The private package already holds a copy of them with a manifest (Trade path,
Trade commit, sha256).

Same working rules as always: PAPER only, LIVE untouched, draft PRs, self-review, tests, English only, no secrets in
git or in chat. The session prepares changes and commands; **Abhi runs every command on the DigitalOcean VPS** and
pastes back the results.

**A. Check the copy (the session):**

1. For every file in the manifest, check that Trade's **current `main`** has the same sha256 as the manifest. Any file
   changed since the manifest's commit: stop and report it; the private copy is refreshed first.
2. Check that every file in the private package has the sha256 the manifest lists.

**B. Make Trade read from the private copy (a Trade PR, merged; nothing removed yet):**

3. Add one setting (an environment variable) that names the KB / Method Pack directory (the private package's
   `knowledge/`, a copy of `docs/knowledge/` as is, generated pack included), read by the pack builder
   (`scripts/build_method_pack.py`), `vision/method_pack.py`, the Vision audit and every other reader of
   `docs/knowledge/`. For this PR its default is still the in-tree `docs/knowledge/`.
4. Make the tests that read the KB or the pack (the stale-pack test and any other) skip with a visible reason when
   the setting is unset or the directory is missing, so Trade's public CI keeps working after the removal. Trade's
   public CI never gets a key to the private repository.

**C. Switch the running bot (Abhi on the VPS):**

5. Abhi creates a read-only deploy key on the VPS for the private repository (the public half added in the private
   repository's Settings -> Deploy keys, "Allow write access" unticked; the private half never leaves the VPS and is
   never pasted anywhere), and clones the private repository at the **tag or commit** the session names (never a
   moving branch, so a change to the private repository cannot change Trade's live pack by itself).
6. Abhi sets the setting in the environment of **every Trade service that reads the pack** (the bot's service unit or
   its env file), restarts those services, and shows: the services' environment has the setting; one real cycle
   where the Vision audit loaded the pack from the private checkout, with a pack hash equal to the manifest's; Trade's
   tests pass on the VPS with the setting.
7. **Abhi confirms in writing** that the bot now reads the private copy. Nothing is removed before this.

**D. Remove from Trade's public tree (a Trade PR, merged only after step 7):**

8. In a normal commit (no history rewrite, no force push), remove:
   - `docs/knowledge/` (KB, Chart Reader method, Abhi's method notes, the generated Method Pack), leaving only a short
     `docs/knowledge/README.md` that says the files moved to the private package (no content);
   - method docs that state the method (for example `docs/reports/v22/METHOD_MAP.md`): list each file in the PR first;
     Abhi approves the list.
   The pack builder code (`scripts/build_method_pack.py`, `vision/method_pack.py`) stays in Trade, pointed at the
   private copy, until open question PR-7 decides about Trade's method code.
9. The same PR removes the in-tree default: the setting becomes required, and a missing or wrong path **fails loudly**
   when a service starts (never a silent fallback); the check runs at service start, not at import, so the tests can
   still import the modules and skip (step 4).
10. Add a CI check in Trade that refuses those paths (except `docs/knowledge/README.md`), so they do not come back.
11. Abhi pulls this Trade change on the VPS only after step 7, and checks the services start and the next Vision audit
    loads the pack.

**From then on:** the KB, the method and the Method Pack are edited only in the private repository (KB diff -> Abhi
approves -> code -> test). Trade's `docs/reader/STABLE.md` records, for each stable tag, the private repository's
commit and the pack hash it was approved with. The VPS moves to a new private tag only when Abhi says so.

**Never:**

- delete anything before step 7 is confirmed;
- rewrite Trade's history or force-push (the old copies stay in the history; that is accepted);
- put a deploy key, token or private file content in git, a PR, a comment, a CI log or the chat.
