# Abhi's standing preferences — binding in EVERY session, read FIRST: OWNER_RULES.md
@OWNER_RULES.md

# CLAUDE.md - standing rules for every Claude Code session on ATP

Owner: Abhi. Read at the START of every task and AFTER every compaction (≤ 15 KB): this file and docs/ROADMAP.md
(rules + status), then ONLY the order section the roadmap item points to (README.md is the short index of orders). Full rules:
docs/WORKING_RULES.md. Begin each report with "Rules re-read: CLAUDE.md, roadmap".

## 1. Memory and plan
- docs/owner-orders/ holds everything Abhi ordered: order files, README.md (index -> roadmap IDs), INBOX.md (every
  instruction, verbatim), DECISIONS.md (every decision). INBOX / DECISIONS are append-only: grep by ID or date.
- A new instruction: FIRST append it to INBOX verbatim, in the same turn; then DECISIONS, the roadmap item (new or
  changed) and the README line; commit in a small docs PR. Never delete or rewrite an owner order; mark superseded.
- Roadmap rules R1-R6 (docs/ROADMAP.md): every task has a roadmap ID; max 5 build items in progress (all sessions); "done" = merged +
  flag decided + deployed + checked on the real site + owner's ✅; phases in order; status lives only in the roadmap table.
- Own provisional decisions: docs/OPEN_QUESTIONS.md (current open items only, ≤ 30 KB), each with a default; check them
  against DECISIONS first. Nothing from Abhi lives only in the scratchpad or chat.

## 2. Product
- A business SaaS for retail and beginner traders; 100% user-friendly: show what he needs, hide the rest; no developer
  text, ids, sample data or jargon outside "Advanced" / admin. Two groups only: USER and ADMIN (enforced on the server).
- Every page is a "ready-made dish". Think proactively (what brokers / Sensibull / TradingView offer).
- English only in code and UI (strategy interview: EN / MR / HI). Reports to Abhi in plain Marathi.
- Marketplace OFF; it is the last phase.

## 3. Signals, not advice (SEBI)
- The engine only generates signals. Lots, legs, strikes, SL, targets, exits and trade counts come only from the user's
  own settings. Never a trading rule nobody stated (no hidden R:R filters or default targets).
- Signal text in structure / indicator words: no buy / sell / target / stop-loss wording, no single prices. His OWN
  signals may show the symbol and the area as a price range; public / education surfaces: no names, no prices, charts
  older than three months.
  White box templates, no performance claims; legal advice before LIVE.

## 4. Safety
- Phase 0: PAPER only (live_trading OFF, phase0_paper_only ON); LIVE only on Abhi's explicit words.
- Owner principle (binding): no paper / live feature split - build fully now, same code path LIVE; the LIVE switches
  are his alone.
- Exits are never blocked (ADR-0004). AI never places, blocks or changes orders (ADR-0006).
- Public repo: no secrets, server addresses, credentials or strategy-sensitive content (that lives in atp-private).
- Never run anything on the server; Abhi runs ONE command. Say whether a migration is needed (any time until practice starts; then after
  15:30 IST - DECISIONS 25 Oct). ATP uses its OWN Upstox app, never the Trade bot's.

## 5. Trading logic (Trade reader port)
- Price action by structure, one continuous unit; no day / session / clock framing; numbers only in settings; RSI
  divergence is the only indicator in Abhi's method; never merge areas / bases / timeframes (Z-1u). Generic templates may
  use indicator parameters as user settings.

## 6. How to work (token budget, owner 24 Oct)
- A big spec: an independent review against the code first, fix the findings, then build. Keep what is built; extend.
- Self-review every diff; tests for everything (no look-ahead; parity scan = backtest = live). UI: before / after
  screenshots, in ONE comparison page per milestone.
- Read by grep + line ranges, never a whole large file.
- Agents: at most 2 at a time; mechanical work on the sonnet helpers in .claude/agents/; design, review, trading logic
  and order paths on the main model; each agent writes only to its own folder.
- Tests: only the touched tests locally; CI runs the full suites; long output to a file; re-run only a failing test.
- No polling; background runs. At most 10 images viewed per PR.
- Reports ≤ 25 lines: (1) roadmap items changed; (2) max 3 owner decisions with defaults; (3) deploy line + migration.
- At each milestone: a ≤ 1 KB docs/HANDOVER_NOW.md, then continue in a fresh session.

## 7. PRs and deploy
- One `claude/<name>` branch and one draft PR per item; merge when CI is green (DECISIONS 23 Oct), except where the
  owner holds a ✅. With every PR: the deploy line and migration yes / no.
- Deploy (Abhi, detached): `su - atp -c "nohup sh -c '/opt/atp/deploy/atp.sh deploy
  && /opt/atp/deploy/atp.sh health' > /tmp/atp-deploy.log 2>&1 &"`, then `tail -n 15 /tmp/atp-deploy.log`. Production: https://aialgodesk.tech. New user-facing work goes behind `ui_v2`.
- CI checks and guards: docs/WORKING_RULES.md. Text sizes and colours only from the tokens.

## 8. Keep what was built
- Never delete files or features; never rewrite working logic. A MAJOR behaviour change (trading logic, order paths,
  auth, data model, removing a page) needs Abhi's approval first.
