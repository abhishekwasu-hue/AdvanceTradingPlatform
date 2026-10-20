# Order for a Trade session: move the KB, the Method Pack and the method docs out of Trade's public tree

Abhi pastes this into a Trade session **after** the private package holds a checked copy of the files (steps 1 and 2
of section 8 of `docs/design/PRIVATE_PACKAGE.md` in ATP). It is not to be run before.

---

From Abhi, for the Trade repository (English).

Owner decision PR-1 (20 Oct): the Chart Reader, the KB and the Method Pack live in a separate **private** repository
(the "private package"). The Trade repository is public, so its KB, Method Pack and method docs leave Trade's public
tree. The private package already holds a copy of them, checked against a manifest (Trade path, Trade commit,
sha256).

Same working rules as always: PAPER only, LIVE untouched, draft PRs, self-review, tests, English only, no secrets in
git or in chat.

**Before removing anything:**

1. Read the manifest in the private package. For every file it lists, check that Trade's file at the manifest's commit
   has the same sha256. If Trade changed a file since that commit, stop and report it: the private copy must be
   refreshed first.
2. Make Trade read the Method Pack and the KB from the private checkout instead of its own tree:
   - a setting (an environment variable, default: the current in-tree path) for the pack builder
     (`scripts/build_method_pack.py`), `vision/method_pack.py`, the Vision audit, and every other reader of
     `docs/knowledge/`;
   - the private checkout on the DigitalOcean VPS, cloned with a **read-only deploy key** that Abhi adds (Abhi
     creates the key on the VPS and adds its public half as a deploy key without write access; the session never
     sees the private half);
   - the stale-pack test reads from the same setting.
3. Show on the VPS, with the setting pointing at the private checkout: the Trade tests pass, the Method Pack hash
   equals the hash recorded in the manifest, and the bot's Vision audit loads the pack. Report the results to Abhi.

**Only after steps 1-3 pass and Abhi confirms:**

4. Remove from Trade's public tree, in a normal commit (no history rewrite, no force push):
   - `docs/knowledge/` (KB, Chart Reader method, Abhi's method notes, the generated Method Pack);
   - method docs that state the method (for example `docs/reports/v22/METHOD_MAP.md`); list each file in the PR
     before removing it, and Abhi approves the list;
   - leave a short `docs/knowledge/README.md` that says the files moved to the private package (no content).
5. Add a CI check in Trade that refuses those paths, so they do not come back.

**Never:**

- delete anything before steps 1-3 are done and confirmed;
- rewrite Trade's history or force-push (the old copies stay in the history; that is accepted);
- put a deploy key, token or private file content in git, a PR, a comment or the chat.
