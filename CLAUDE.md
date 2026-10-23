# CLAUDE.md — standing rules for every Claude Code session on ATP

Owner: Abhi. These rules are permanent. They live here because instructions given only in chat are lost after a
context compaction or in a new session. Read this file, then docs/owner-orders/README.md, at the START of every task and
again AFTER every compaction, before acting. Begin each report to Abhi with: "Rules re-read: CLAUDE.md, owner-orders".

## 1. Memory protocol (never lose an instruction)
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

## 2. Product
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

## 3. Signals, not advice (SEBI)
- The strategy engine only GENERATES SIGNALS. Lots, legs, strikes, SL, targets, exits and the number of trades come
  only from the user's own deployment / dashboard settings. Never add a trading rule nobody stated (no hidden R:R
  filters or default targets).
- Signal text uses structure / indicator words only: no buy / sell / target / stop-loss wording, and no single prices.
  On the user's OWN signals (his adopted strategy, his chosen symbols), the symbol and the AREA as a price range may be
  shown. On public / education surfaces: no security names and no prices; examples use charts older than three months.
- Templates and ideas are white box (the full logic is visible); provenance is recorded honestly; no performance claims.
- Information and education, never advice. Legal advice is needed before LIVE / public launch (docs/COMPLIANCE_IN.md).

## 4. Safety
- Phase 0: PAPER only (live_trading OFF, phase0_paper_only ON). LIVE untouched without Abhi's explicit words.
- Owner principle (23 Oct, binding; DECISIONS): no paper-phase vs live-phase feature split. Build every feature fully now; it
  works on the PAPER execution path today and LIVE unchanged (same code path and UI) when Abhi himself switches LIVE on.
  Never defer, shrink or drop a feature because the account is in PAPER. The LIVE switches stay his alone.
- Exits are never blocked (ADR-0004). AI never places, blocks or changes orders (ADR-0006).
- No secrets in git / logs / chat. This repo is PUBLIC: no strategy-sensitive content (KB, Method Pack, golden fixtures,
  charts, journal), no server addresses or credentials; the private method lives in atp-private.
- Never run anything on the server yourself. Abhi runs ONE consolidated command. Say whether a migration is needed
  (if yes: after 15:30 IST, unless Abhi says otherwise).
- ATP uses its OWN Upstox app / API key, never the Trade bot's (a new login there kills the bot's token). Default data
  connection: the Upstox Analytics Token.

## 5. Trading logic (when the Trade reader is ported)
- Abhi is a price-action trader, not an intraday trader. Chart reading is by structure, as one continuous unit: no day
  / date / session / clock-time framing and no fixed numbers in reading rules (numbers live only in settings).
  RSI divergence is the only indicator in Abhi's own method. Never merge areas / bases / timeframes (Z-1u).
- Generic templates for other users may use indicator parameters, but only as user settings.

## 6. How to work
- Before a big spec goes to code: an independent review of it against the real code (a separate agent), fix the
  findings, then build.
- Self-review every diff before pushing. Tests for everything (no look-ahead; parity scan = backtest = live where it
  applies). For UI: before / after screenshots (desktop + phone, light + dark).
- Work in parallel worktrees; never sit idle; log questions with a provisional default and continue. No frequent
  "still waiting" check-ins (they cost credits); report when something real is ready.
- Keep what is built; extend, don't rewrite.

## 7. PRs, deploy and environment (folded in from #330)
- One `claude/<name>` branch per item, one PR per item (owner, 22 Oct). Draft PRs; merge only on Abhi's "Merge".
  With every PR: the deploy line below and whether a migration is needed.
- The one deploy command Abhi runs on the host:

  ```
  su - atp -c "/opt/atp/deploy/atp.sh deploy && /opt/atp/deploy/atp.sh health"
  ```

  A migration: Abhi runs it after 15:30 IST (`MIGRATION_FORCE=1` only when he says so).
- Production: https://aialgodesk.tech (Hostinger KVM 2, `/opt/atp`, user `atp`; the www redirect is live). R2 off-site
  backup is not set up yet (owner).
- Flags turned OFF by Abhi on 21 Oct: `self_signup`, `scan_auto_paper`, `marketplace`, `public_api`. `ui_v2` is ON. New
  user-facing work goes behind `ui_v2`.
- Google sign-in: the button stays hidden until Abhi adds the OAuth credentials (steps in docs/OPEN_QUESTIONS.md, GS-1).
- Where things are:
  - Backend `backend/` (FastAPI `app/`, tests `backend/tests`, Alembic `alembic/`). CI: `ruff check app tests evals`,
    `mypy`, `bandit`, `pytest`, `alembic check`, `python backend/scripts/migration_rollback_safety.py lint`.
  - Frontend `frontend/` (React + Vite + vitest + Playwright). CI: `npm test`, `npm run check:v2-colours`,
    `npm run check:mobile`, `npm run build`, `npm run check:bundle`, `npm run test:e2e`. Text sizes only from the
    Tailwind tokens (rem; never `text-[Npx]` below 14 px); colours only from the tokens.
  - Guards: `scripts/check_english_only.py`, `scripts/check_public_repo.py`.
  - Deploy: `deploy/atp.sh`; runbook `docs/deploy/HOSTINGER_RUNBOOK.md`. Working rules: `docs/WORKING_RULES.md`.

## 8. Keep what was built (owner, 22 Oct)
- Much work was done before this session. Never delete existing files or features, and never rewrite working logic.
- Before any MAJOR change that alters existing behaviour (trading logic, order paths, auth, data model / migrations,
  removing or replacing a page), say what changes and get Abhi's approval first. Small fixes and additions behind a
  flag need no extra approval. We improve; we never break.
