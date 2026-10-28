# Owner order BR - "Trading Brain" (28 Oct 2026, verbatim)

Roadmap: phase BR (BR-0 ... BR-3). Starts only after BR-0 (the foundation) is done; until then: filed, planned, and the
independent review of this spec against the code (owner ✅ before any build).

```
OWNER ORDER — "TRADING BRAIN" (28 Oct). New roadmap phase BR (BR-0 … BR-3), owner-approved. Starts ONLY after BR-0 (the foundation)
is done; until then file it and plan only. File in INBOX verbatim / DECISIONS / roadmap / README first; full text in
docs/owner-orders/ATP_ORDER_TRADING_BRAIN.md. Major architecture: an independent review of this spec against the code first
(≤ 25 lines, what exists / what to extend / risks), owner ✅, then build. EXTEND what exists — market_memory (Phase AR), coach (AV),
interview (AP), monitor (L4), journal v2 (P2-8), evidence / grounding — never a parallel system.

GOAL: Autopilot and the AI Copilot think like an experienced trader: see (one reader), remember (every signal / trade / skip with its
context and outcome), recall similar past setups, reason with a second opinion, reflect after each trade, and know how sure they are —
proven with charts, not claims. Research basis: layered memory + reflection (FinMem / TradingGPT, arXiv 2309.03736), multi-agent
debate (TradingAgents, arXiv 2412.20138), selective consensus (TrustTrade, arXiv 2603.22567), setup-level journal analytics.

BINDING LIMITS (unchanged): AI never places, blocks or changes orders (ADR-0006); exits never blocked (ADR-0004); a learned lesson is
NEVER applied automatically — it becomes a proposal the user approves (✅) before it changes anything; lots / SL / target / exits only
from the user's own settings, no hidden rules (TT-12); signals-not-advice wording (SEBI); multi-tenant: every memory, profile and
lesson belongs to one user (tenant + user_id scoped, never shared across users without explicit opt-in); public repo: no owner
strategy content in code, prompts or fixtures (his method lives only in his own account's private profile); English UI (strategy
interview EN / MR / HI); no look-ahead (memory of a setup is written only from bars closed at that time).

BR-0 FOUNDATION (prerequisite, already ordered): SCR-FIX (data path / intervals), AP-FIX, CP-FIX; DATA-H lake backfill; the Trade
reader port (P5-1) passing its answer-key score. No BR-1 work before these are merged + deployed + checked.

BR-1 MEMORY (perceive + remember + recall)
M1 One shared reader for every surface (Screener, Autopilot, Copilot, Signals): structure per TF, areas, lifecycle, pullback, trigger —
   the same function and the same output everywhere (parity tests).
M2 Episodic memory: every signal, approval card, skip (with reason), entry, exit and outcome is stored as an "episode" with its context
   snapshot at decision time (reader output per TF, area, distance to the next area, invalidation, regime / weekly state, event and
   expiry distance, global cues line, data age) + the result (P&L in R, MFE / MAE, how it exited). Extends journal capture (J1), not a
   new store. Retention + export + delete per user.
M3 Similar-setup recall: for a new setup, find the user's own past episodes with the most similar context (structured features first —
   same model, area type / state, trend side, room to the next area, gap / opening, expiry distance; embeddings optional and only as a
   second step) and show "N similar: W won / L lost, avg R, common reason for losses" with links to their charts. Below a minimum N:
   say "not enough history yet" (never extrapolate).
M4 User method profile (replaces any "owner handbook"): each user's own method, built from the strategy interview + their saved
   strategy settings + their own written rules (plain words, editable, versioned). The Copilot reasons with THIS profile plus general,
   verified trading knowledge, and always says which it used. No profile yet → general education answers + "set up your method".
   The owner's profile is private data in his account, never in the repo.
M5 Working memory in chat: the last symbol, job and open question carry over ("and BANKNIFTY?", "why?").

BR-2 REFLECTION + CALIBRATION (needs ≥ 20 closed PAPER episodes per user; below that, show progress "12 of 20")
R1 Post-trade reflection: after each exit, one plain line "what the context said vs what happened" (from M2 facts, grounded; e.g.
   "entered with 8 points of room to the next demand; price stopped there"). Extends coach / TP-8.
R2 Weekly lessons: patterns across the user's episodes (e.g. "entries after a gap open: 2 of 9 won") as PROPOSALS with the evidence
   episodes; the user accepts (→ added to his method profile as his own rule, versioned) or dismisses. Nothing changes without ✅.
R3 Calibration: every confidence the system shows (Vision, setup score, Copilot) is logged with its outcome; a reliability chart
   (stated confidence vs actual hit rate, Brier score) per source per user; a source whose confidence is not calibrated shows no number,
   only words ("uncertain"), until it is.
R4 "I don't know": when data is missing, stale or the context has no precedent, say so plainly instead of answering.

BR-3 SECOND OPINION + STATE + PROACTIVE
S1 Setup state machine (Autopilot + Signals): watching → in area → pullback complete → triggered → managing → exited, per setup,
   persisted; every transition logged with its reason ("Why no trade" timeline = this log). Replaces stateless bar-by-bar checks where
   they exist; byte-identical decisions on stored history before switching (parity).
S2 Pre-trade check (shown on the card, information only): next area and room to it, invalidation, similar-setup recall (M3), calibrated
   confidence (R3), the user's own relevant lessons (R2). Never a gate unless the user turned that rule on himself.
S3 Second opinion: a "for" and an "against" reading of the same setup (two prompts or Vision as critic) shown side by side when they
   disagree; disagreement lowers the shown confidence, never blocks.
S4 Proactive brief + alerts from the user's own open positions: each short strike's distance to the nearest area, expiry distance,
   events, global cues; "what changed since yesterday". Delivered in app + Telegram (in-place connect).

PROOF (no claim without a chart): per BR step, ONE comparison page: (1) reader answer-key score before / after; (2) recall examples
with their charts; (3) calibration reliability chart; (4) the same PAPER period with and without the Brain information (decisions are
still the user's — measure only what the information changed in approvals / skips, never auto-trade); phone 390 px + desktop.
Tests: per-user isolation (no cross-user memory), no look-ahead in episodes, proposals never auto-applied, grounding of every number.
Cost: per-user AI cost per day shown and capped (user setting); reuse stored results; cheap model for summaries, main model for
reasoning. Reports ≤ 25 lines; each BR step: deploy line + migration yes / no (episodes / profiles / calibration tables need one —
after 15:30 IST).
```
