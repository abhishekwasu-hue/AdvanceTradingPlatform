# CLAUDE.md — standing rules for every ATP session

Owner: Abhi. These rules apply to every Claude Code session on this repository. They add to
`docs/WORKING_RULES.md` (multitasking, self-review, fixtures) and `docs/PUBLIC_REPO_POLICY.md`.

## Before any coding

- Read `docs/WORK_LOG.md` (tail), `docs/OPEN_QUESTIONS.md`, `docs/deploy/HOSTINGER_RUNBOOK.md`,
  `docs/design/UI_V2_PROPOSAL.md`, the recent `git log` and the open PRs.
- For each item you are asked to build, say first whether it is done / partial / not started, with evidence.

## Product

- ATP (AiAlgoDesk) is a business SaaS for retail and beginner traders. **100% user-friendly is the top rule**:
  a user logs in to work; show what they need, make the important things stand out, hide or remove everything else.
  No developer text, internal ids, sample data or technical terms outside "Advanced" or the admin console.
- Exactly **two groups**:
  - **USER**: the trading view, the same for beginners and pros; beginners learn through the page tours.
  - **ADMIN** (SUPER_ADMIN): everything technical — system, flags, users / business, testing tools, diagnostics,
    "view as user". Enforced on the server (`require_role` + MFA), never only in the UI.
- Every page serves a "ready-made dish": prepared, personal information the moment it opens.
- Think proactively: when building a feature, look at what brokers / Sensibull / StockEdge / TradingView offer and
  add what makes trading easier. Never only the literal minimum.
- English only in code and UI (the one exception: the strategy interview, EN / MR / HI). Reports to Abhi are in
  plain Marathi (in chat, never in the repository: `scripts/check_english_only.py` guards the tree).

## Safety

- **Phase 0: PAPER only.** `live_trading` OFF, `phase0_paper_only` ON. Do not touch LIVE without Abhi's explicit
  words. Exits are never blocked (ADR-0004). AI never places, blocks or changes orders (ADR-0006).
- Information and education, never buy / sell advice for general users (SEBI; the compliance pack BT7 decides later).
- No secrets in git, logs or chat. **The repository is PUBLIC**: no strategy-sensitive content (KB, Method Pack,
  golden fixtures, charts, journal). The private method lives in `atp-private`.
- Never run anything on the server yourself. Abhi runs ONE consolidated command:

  ```
  su - atp -c "/opt/atp/deploy/atp.sh deploy && /opt/atp/deploy/atp.sh health"
  ```

  With every merge, say whether a migration is needed. If yes: deploy after 15:30 IST, or `MIGRATION_FORCE=1` only
  when Abhi says so.

## Signal language and the marketplace (owner decision, handover 11l)

- The marketplace stays OFF. It is the LAST phase, considered only about six months from now, once every feature is
  complete and production-grade. Don't build marketplace features before then.
- Showing strategies / ideas is EDUCATION. "Buy this stock, SL here, target there" is research / advice: ATP never
  does it. The strategy engine only GENERATES SIGNALS from logic the user adopted and configured; the user decides the
  trade, lots and execution.
- Signals are described in STRUCTURE WORDS only (e.g. "the pullback reached a seller area; a handover is forming").
  No single prices or other numbers, and no buy / sell / target / stop-loss wording anywhere.
  - Public / education surfaces (Idea Library, Copilot teacher, brief text, learning examples, marketing, share
    cards): no security names and no prices; learning examples use anonymised charts older than 3 months (SEBI,
    Jan 2025).
  - The user's OWN signals (his adopted strategy, his chosen symbols): the symbol is shown, and the AREA may be shown
    as a price range ("Seller area: 23,000–23,100"). What his automation did (orders, lots, fills) is a factual record
    on the Orders / Positions pages, never part of the signal text.
- The engine may use numbers internally; the policy governs what is SHOWN. A formatter and a CI guard enforce it.

## Trading logic (when ATP ports the Trade reader)

- Abhi is a price-action trader, not an intraday trader. Chart reading is by structure; no day / date / time framing;
  no fixed numbers in reading rules (numbers live only in settings).
- R:R, SL, target, lots and exits are the user's own settings. Never add trading rules nobody stated.
- RSI divergence is the only indicator in Abhi's method.

## Work style

- Self-review every diff before pushing (`docs/WORKING_RULES.md` §2); tests for everything; ruff + mypy on touched
  backend files; `npm test` and `npm run build` for frontend changes.
- UI changes: before / after screenshots, desktop + phone, light + dark.
- Work in parallel worktrees; never sit idle. Log questions in `docs/OPEN_QUESTIONS.md` with a provisional default
  and continue. No frequent "still waiting" check-ins; report when something real is ready.
- Keep what is built: extend, don't rewrite. New user-facing work goes behind `ui_v2`.
- Draft / small PR series; merge only on Abhi's "Merge". After each merge, give the one deploy line above.

## Environment facts

- Production: https://aialgodesk.tech (Hostinger KVM 2, `/opt/atp`, user `atp`). The www redirect is live.
  R2 off-site backup is not set up yet (owner).
- Upstox: ATP uses its OWN Upstox app / API key, never the Trade bot's (a new login there kills the bot's token).
  The Analytics Token (one year, data only) is the default connection for data and PAPER.
- Flags turned OFF by Abhi on 21 Oct: `self_signup`, `scan_auto_paper`, `marketplace`, `public_api`. `ui_v2` is ON.

## Where things are

- Backend: `backend/` (FastAPI, `app/`, tests in `backend/tests`, `alembic/` migrations).
  CI runs `ruff check app tests evals`, `mypy`, `bandit`, `pytest`, `alembic check`.
- Frontend: `frontend/` (React + Vite + vitest + Playwright). CI runs `npm test`, `npm run check:v2-colours`,
  `npm run check:mobile`, `npm run build`, `npm run check:bundle`.
- Guards: `scripts/check_english_only.py`, `scripts/check_public_repo.py`.
- Deploy: `deploy/atp.sh`, runbook `docs/deploy/HOSTINGER_RUNBOOK.md`.
