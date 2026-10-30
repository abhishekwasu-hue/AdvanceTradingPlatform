# Working rules (Abhi, standing - apply to every item)

These are in addition to MASTER SPEC §0 (don't stop, OPEN_QUESTIONS with a provisional default, WORK_LOG pushed at least
every 2 hours, draft PRs only, merge only on Abhi's "Merge", no LIVE / VPS / secrets).

## 1. Multitasking is required
- Anything that runs longer than about 2 minutes goes to the background (`run_in_background`, or
  `nohup ... > /tmp/<name>.log 2>&1 &`).
- The next module or item starts at once, in its own `git worktree` (`git worktree add ../wt-<name> -b claude/<name> origin/main`,
  or on that item's branch).
- Read a log only when the result is ready (`tail -n 40 /tmp/<name>.log`). Never poll it in a loop.
- Only one heavy job (full suite, build) runs at a time. Reading code and writing specs, tests and docs are not heavy;
  neither are small targeted tests. Those always continue.
- Waiting for CI is not a reason to stop: start the next item.

## 2. Self-review is required before every push of a finished item
- (a) Re-read the whole diff (`git diff origin/<branch>...HEAD`) as a sceptical reviewer. Check:
  - the logic against the spec;
  - look-ahead and known_at;
  - bar-index off-by-one;
  - timezone (IST);
  - direction and sign (bull put vs bear call, high vs low);
  - silent exceptions;
  - hard-coded dates or figures;
  - tests that can never fail.
  For ATP, also check:
  - tenant isolation;
  - blocking calls inside async code;
  - exits never blocked (ADR-0004);
  - no AI action without approval (ADR-0006);
  - LIVE flags off by default;
  - reversible migrations.
- (b) Run the new tests and the module's existing tests. For ATP, also run ruff and mypy on the files touched.
- (c) Put a 5-10 line "self-review" note in the commit message or PR body: what was checked, what was fixed.
- (d) Anything for Abhi goes to OPEN_QUESTIONS.
- After the push, if time allows, do a second independent pass on the same diff (fresh eyes or a reviewer subagent).
  Anything found goes in a follow-up commit.

## 3. Test fixtures
- Golden fixtures over ~1,000 lines are stored compressed (`.json.gz`, written deterministically: sorted keys, gzip
  `mtime=0`, loaded by the test) or generated deterministically from the pinned source commit - never committed as big
  plain JSON. The fixture folder's README says how to regenerate them.

## Queue notes from Abhi (order)
- **IBKR adapter (MASTER v1.2 §2)** comes after the Indian PAPER go-live and crypto Phase 1. The design PR comes first
  (ADR-0018 IBKR transports). The spec is in `docs/specs/IBKR_ADAPTER_SPEC.md`.
- **Crypto adapters (v1.3 §3)**, in this order:
  1. CoinDCX (exists);
  2. Binance spot;
  3. Bybit / KuCoin / OKX (perps and options only in allowed jurisdictions);
  4. CoinSwitch PRO / ZebPay.

  Kraken comes later.
  - The exchange registry carries `fiu_registered` (config, with its source and date).
  - An India resident gets only fiu_registered exchanges, and spot only.
  - Tax: a 30 % + 1 % TDS report seam (P2).

## Token budget (owner, 24 Oct; binding - DECISIONS TB-1..TB-9)
1. Start-of-task reading ≤ 15 KB: CLAUDE.md (≤ 5 KB) + docs/ROADMAP.md + docs/owner-orders/README.md (a ≤ 8 KB index),
   then ONLY the section of the order the roadmap item points to. History: docs/owner-orders/README_HISTORY.md.
2. docs/OPEN_QUESTIONS.md holds only current open items (≤ 30 KB). Answered rows move to
   docs/archive/OPEN_QUESTIONS_CLOSED.md; older open rows with their default in effect live in
   docs/archive/OPEN_QUESTIONS_PROVISIONAL.md. INBOX.md / DECISIONS.md are append-only: grep by ID or date, never read whole.
3. Code: grep + line ranges; never cat a whole large file; never re-read a file just edited.
4. Agents: at most 2 at a time. Mechanical work (tests, screenshots, docs / status updates, copying) goes to the sonnet
   helpers in .claude/agents/; design, review, trading / reader logic and order paths stay on the main model. Each agent
   writes only to its own folder.
5. Tests: only the tests touching the change locally; CI runs the full suites. Long output to a file, show only the
   failure summary. Re-run only the single failing test, never a whole suite for a flake.
6. No polling: long runs in the background; wait for the completion notice.
7. Images: view at most 10 per PR; galleries ≤ 20 named charts per fix; one screenshot comparison page per milestone.
8. Reports ≤ 25 lines, 3 parts: (1) roadmap items changed (ID, old -> new status); (2) max 3 owner decisions, each with a
   default; (3) the deploy line + migration yes / no. No narration, no test-count lists, no repeated background.
9. At each milestone: a ≤ 1 KB docs/HANDOVER_NOW.md (in flight, next step, open PRs), then continue in a fresh session.

## CLAUDE.md before the 24 Oct trim (moved here, never deleted)

# CLAUDE.md — standing rules for every Claude Code session on ATP

Owner: Abhi. These rules are permanent. They live here because instructions given only in chat are lost after a
context compaction or in a new session. Read this file, then docs/owner-orders/README.md, at the START of every task and
again AFTER every compaction, before acting. Begin each report to Abhi with: "Rules re-read: CLAUDE.md, owner-orders".

#### 1. Memory protocol (never lose an instruction)
- docs/owner-orders/ is the single home of everything Abhi has ordered: the order files, README.md (the index + the
  status of every item), INBOX.md (every new instruction, verbatim) and DECISIONS.md (every owner decision).
- When Abhi sends ANY new instruction, prompt, template or decision: FIRST append it to INBOX.md verbatim (date, the
  text, the files attached), in the same turn and before any other work. Then file it: a new order file or a section in
  an existing one, a line in DECISIONS.md, and its status row in README.md. Commit it in a small docs PR (or in the
  current PR).
- Never delete or rewrite an owner order. Mark items done / superseded in README.md, with the PR. When an instruction
  replaces an older one, say so in both places.
- Your own provisional decisions go in docs/OPEN_QUESTIONS.md. Before taking one, check it against this file,
  DECISIONS.md and the order files, and list that check in the report.
- Keep the scratchpad for temporary work only; nothing Abhi gave may exist only in the scratchpad or in chat.

#### 2. Product
- ATP is a business SaaS for retail and beginner traders. 100% user-friendly is the top rule: a user logs in to work;
  show what he needs, make the important things stand out, hide or remove everything else. No developer text,
  internal ids, sample data or technical terms outside "Advanced" / the admin console.
- Exactly TWO groups: USER (one trading view for beginners and pros; beginners learn through the page tours) and ADMIN
  (SUPER_ADMIN: everything technical). Enforced on the server.
- Every page gives a "ready-made dish": prepared, personal information on open.
- Think proactively: when building a feature, research what brokers / Sensibull / StockEdge / TradingView / Tradetron
  offer and add what makes trading easier. Never only the literal minimum.
- English only in code and UI (except the strategy interview: EN / MR / HI). Reports to Abhi in plain Marathi.
- The marketplace stays OFF; it is the LAST phase (about six months out, once everything is production-grade).

#### 3. Signals, not advice (SEBI)
- The strategy engine only GENERATES SIGNALS. Lots, legs, strikes, SL, targets, exits and the number of trades come
  only from the user's own deployment / dashboard settings. Never add a trading rule nobody stated (no hidden R:R
  filters or default targets).
- Signal text uses structure / indicator words only: no buy / sell / target / stop-loss wording, and no single prices.
  On the user's OWN signals (his adopted strategy, his chosen symbols), the symbol and the AREA as a price range may be
  shown. On public / education surfaces: no security names and no prices; examples use charts older than three months.
- Templates and ideas are white box (the full logic is visible); provenance is recorded honestly; no performance claims.
- Information and education, never advice. Legal advice is needed before LIVE / public launch (docs/COMPLIANCE_IN.md).

#### 4. Safety
- Phase 0: PAPER only (live_trading OFF, phase0_paper_only ON). LIVE untouched without Abhi's explicit words.
- Owner principle (23 Oct, binding; DECISIONS): no paper-phase vs live-phase feature split. Build every feature fully now; it
  works on the PAPER execution path today and LIVE unchanged (same code path and UI) when Abhi himself switches LIVE on.
  Never defer, shrink or drop a feature because the account is in PAPER. The LIVE switches stay his alone.
- Exits are never blocked (ADR-0004). AI never places, blocks or changes orders (ADR-0006).
- No secrets in git / logs / chat. This repo is PUBLIC: no strategy-sensitive content (KB, Method Pack, golden fixtures,
  charts, journal), no server addresses or credentials; the private method lives in atp-private.
- Never run anything on the server yourself. Abhi runs ONE consolidated command. Say whether a migration is needed
  (if yes: any time while nobody practises on ATP yet; from the day Abhi says practice started, after 15:30 IST
  on trading days - DECISIONS 25 Oct).
- ATP uses its OWN Upstox app / API key, never the Trade bot's (a new login there kills the bot's token). Default data
  connection: the Upstox Analytics Token.

#### 5. Trading logic (when the Trade reader is ported)
- Abhi is a price-action trader, not an intraday trader. Chart reading is by structure, as one continuous unit: no day
  / date / session / clock-time framing and no fixed numbers in reading rules (numbers live only in settings).
  RSI divergence is the only indicator in Abhi's own method. Never merge areas / bases / timeframes (Z-1u).
- Generic templates for other users may use indicator parameters, but only as user settings.

#### 6. How to work
- Before a big spec goes to code: an independent review of it against the real code (a separate agent), fix the
  findings, then build.
- Self-review every diff before pushing. Tests for everything (no look-ahead; parity scan = backtest = live where it
  applies). For UI: before / after screenshots (desktop + phone, light + dark).
- Work in parallel worktrees; never sit idle; log questions with a provisional default and continue. No frequent
  "still waiting" check-ins (they cost credits); report when something real is ready.
- Keep what is built; extend, don't rewrite.

#### 7. PRs, deploy and environment (folded in from #330)
- One `claude/<name>` branch per item, one PR per item (owner, 22 Oct). Draft PRs; merge only on Abhi's "Merge".
  With every PR: the deploy line below and whether a migration is needed.
- The one deploy command Abhi runs on the host:

  ```
  su - atp -c "/opt/atp/deploy/atp.sh deploy && /opt/atp/deploy/atp.sh health"
  ```

  A migration: any time until practice starts (DECISIONS 25 Oct); then after 15:30 IST (`MIGRATION_FORCE=1` only when he says so).
- Production: https://aialgodesk.tech (Hostinger KVM 2, `/opt/atp`, user `atp`; the www redirect is live). R2 off-site
  backup is not set up yet (owner).
- Flags turned OFF by Abhi on 21 Oct: `self_signup`, `scan_auto_paper`, `marketplace`, `public_api`. `ui_v2` is ON. New
  user-facing work goes behind `ui_v2`.
- Google sign-in: the button stays hidden until Abhi adds the OAuth credentials (steps in docs/OPEN_QUESTIONS.md, GS-1).
- Where things are:
  - Backend `backend/` (FastAPI `app/`, tests `backend/tests`, Alembic `alembic/`). CI: `ruff check app tests evals`,
    `mypy`, `bandit`, `pytest`, `alembic check`, `python backend/scripts/migration_rollback_safety.py lint`.
    Lint (AUD-G1.6): ruff `select` in `backend/ruff.toml` (adds ASYNC and I - keep imports sorted, `ruff check --fix`);
    pyupgrade (UP) is a ratchet, not a gate: `backend/tests/test_ruff_up_ratchet.py` (CI lint job) fails when any UP
    rule's count rises - write new code with `list[...]`, `X | None`, `datetime.UTC`, and lower the baseline when a
    count falls. mypy gates only the packages in `backend/mypy.ini` `files`; add a package once `mypy app/<pkg>` is clean.
    Dependencies (M55 O3): `backend/requirements.txt` lists the direct ones (ranges; Dependabot edits it);
    `backend/requirements.lock` pins everything with hashes and is what the image and CI install (`--require-hashes`).
    After any change to requirements.txt run `sh scripts/lock_requirements.sh` in `backend/` (needs `uv`; `--upgrade`
    re-resolves all) and commit the lock in the same PR - the CI lint job fails while they disagree.
  - Frontend `frontend/` (React + Vite + vitest + Playwright). CI: `npm test`, `npm run check:v2-colours`,
    `npm run check:mobile`, `npm run lint`, `npm run build`, `npm run check:bundle`, `npm run test:e2e`.
    `npm run lint` (AUD-G1.6) = ESLint + typescript-eslint (`frontend/eslint.config.js`): errors fail; the warnings the
    code had are a per-rule ratchet (`frontend/scripts/check-eslint-ratchet.mjs`) that may only fall. Text sizes only from the
    Tailwind tokens (rem; never `text-[Npx]` below 14 px); colours only from the tokens.
  - Guards: `scripts/check_english_only.py`, `scripts/check_public_repo.py`.
  - Deploy: `deploy/atp.sh`; runbook `docs/deploy/HOSTINGER_RUNBOOK.md`. Working rules: `docs/WORKING_RULES.md`.

#### 8. Keep what was built (owner, 22 Oct)
- Much work was done before this session. Never delete existing files or features, and never rewrite working logic.
- Before any MAJOR change that alters existing behaviour (trading logic, order paths, auth, data model / migrations,
  removing or replacing a page), say what changes and get Abhi's approval first. Small fixes and additions behind a
  flag need no extra approval. We improve; we never break.
