# N2: P1-f / P1-g / P1-h - progression ladder, decision tools, context panel, workspace polish, education

Spec: `docs/specs/ATP_PROFITABILITY_MANUAL_TRADING_OSB_SPEC.md` §1.4, §1.5, §1.6, §2.1-§2.6, §3.2 (second half);
Phase 0 freeze §3 (N2, after Z11). Builds on:

- Z3-b PAPER account and ladder card (`app/paper/service.py`, `PaperAccountCard.tsx`, #174);
- P1-c builder (`app/options_builder`, `OptionsBuilderPage.tsx`, #170 / #180);
- P1-d ticket and risk-by-default (`app/paper/ticket.py`, `app/risk_engine/by_default.py`, #179 / #195);
- Z6 journal, behaviour tags, discipline score and weekly review (`app/journal`, #171 / #172).

Standing rules for every slice:

- Information only, never advice. The AI places no order (ADR-0006), and exits are never blocked (ADR-0004).
- PAPER only in Phase 0. No slice adds a broker order path.
- One default-off flag per slice, listed in `DEFAULT_OFF_FLAGS`. With the flag off, nothing changes (each slice tests
  this).
- Every number is the user's own (user scope, as Z3-b and Z6). Nothing is ranked against other users.
- UI strings are English and go through an i18n instance (react-i18next, the Copilot's pattern). The Devanagari check,
  bundle budget, vitest and the frontend-only build pass.
- Each slice is one PR under about 2,500 changed lines. Each PR has tests, a WORK_LOG entry, and open questions with
  provisional defaults.

## Slices (in build order)

| # | Spec | Slice | Flag | Migration / new routes | Size |
|---|---|---|---|---|---|
| **N2-1** | §1.4, §1.5 | **Ladder evidence** (this PR, `claude/n2-p1f`): adds the discipline-score criterion from the Z6 journal, plus the user's own figures (expectancy, profit factor, payoff ratio, Kelly reference, risk-of-ruin bound) on the ladder card. | `ladder_evidence` | none / none (`GET /api/paper/ladder` gains keys while on) | ~1.2k |
| **N2-2** | §1.4 | **Experience levels** (`claude/n2-2`): Beginner / Intermediate / Advanced, derived from the user's own PAPER record. They gate futures (Intermediate) and naked short options (Advanced), single-leg and structures, in the order pipeline. An explicit opt-out for the IST day, with a reason, is logged (audit) and counted. Exits are never gated. | `experience_levels` | table `experience_opt_outs` (`ROLLBACK_SAFE = True`) / `GET /api/paper/experience`, `POST /api/paper/experience/opt-out` (tenancy table) | ~1.3k |
| N2-3 | §1.5 | **Decision tools I**: risk-of-ruin and Kelly calculators with editable inputs, prefilled from N2-1's figures, and a what-if for any open position (price / IV / days-forward, with T+0 lines) that reuses `options_builder.model` (BS, PoP, expected move). | `decision_tools` | none / `POST /api/decision/what-if`, `POST /api/decision/ruin` | ~1.8k |
| N2-4 | §1.5 | **Decision tools II**: a correlation and exposure view across open positions (by underlying, sector, beta to NIFTY from lake EOD bars), plus a probability cone and expected-move overlay on the main chart (a CHARTING layer, the builder's cone maths). | `decision_tools` | none / `GET /api/decision/exposure` | ~1.6k |
| N2-5 | §1.5 | **Market context panel**: one tile each for regime (Dow on W / D, `ai.regime`), breadth (advance / decline from the lake EOD), India VIX and IV rank (the vix_gate reading, chain IV history), FII / DII flows (NSE provider, daily cache), global cues (`ai/global_cues`) and events today / this week (`news_events`). Each tile shows its source, as-of time and stale state, and says NA on its own failure. | `context_panel` | none / `GET /api/context/panel` | ~2.0k |
| N2-6 | §2.4 | **Positions v2**: live P&L in ₹ and R, Greeks per position and for the portfolio, SL / target status, time in trade, plan vs current (a drifted stop is highlighted) and an exit ladder. Exits stay available during a lockout (a test covers this). | `positions_v2` | none / none | ~1.8k |
| N2-7 | §3.2 | **Builder adjustments**: roll a leg (up / down / out), add a hedge, convert to an iron condor, close one side. Each shows the before / after payoff and Greeks. Pure functions with golden tests. Fills the P1-c "Adjustments and compare" drawer. | `builder_adjustments` | none / `POST /api/options-builder/adjust` | ~1.8k |
| N2-8 | §3.2 | **Builder compare**: up to three strategies side by side (payoff, PoP, max loss, margin, theta per day, breakevens). Saved strategies when `options_builder_saved` is on. | `builder_compare` | none / none (client-side over `/payoff`) | ~1.2k |
| N2-9 | §2.1, §2.5 | **Trade workspace**: chart on the left, chain and ticket on the right, positions strip at the bottom. Keyboard shortcuts: ticket, close position, flatten all with confirmation. On a phone, the ticket is a bottom sheet. States: pre-market, closed, lockout (why and until when), stale data. Layouts are saved per viewer (localStorage). Visual regression in dark, light and mobile. | `trade_workspace` | none / none | ~2.2k |
| N2-10 | §1.6 | **Education pages**: method library (trend -> level -> pullback -> commitment -> R:R) and a glossary. Each concept links to the chart layer that draws it (zones, evidence) and the screens that find it (taxonomy / Legends). Static English content with a compliance scan. | `education_pages` | none / none | ~2.0k |
| N2-11 | §1.6, §1.3 | **Learn from your journal**: cards that link a Z6 behaviour tag on the user's own losing trades to its lesson (N2-10). The weekly review itself shipped in Z6-b (#172); this slice links it to the lessons. | `journal_lessons` | none / `GET /api/journal/lessons` | ~1.0k |

N2-J (the journal module, `docs/journal/PLAN.md`) runs next to N2. N2-11 reads only the Z6 tables, so the two do not
collide.

## N2-1 Ladder evidence (this PR)

**Flag** `ladder_evidence` (off by default). While it is off, `GET /api/paper/ladder` returns exactly what Z3-b
returns: the discipline criterion stays "not measured" and there is no `evidence` key (tested). The ladder still
answers 404 while `paper_account` is off.

**Discipline criterion** (§1.4 "graduation criteria ... discipline score"):

- Value: the mean of the user's Z6 weekly discipline scores over the last `PAPER_LADDER_DISCIPLINE_WEEKS` (default 4)
  completed IST weeks that had a PAPER trade. The look-back is at most `PAPER_LADDER_DISCIPLINE_LOOKBACK_WEEKS`
  (default 12) weeks.
- Met when there are that many scored weeks and the mean is at least `PAPER_LADDER_MIN_DISCIPLINE` (default 70).
- Each week's score comes from `journal.service.refresh_week`, the same code `GET /api/journal/week` runs, so the card
  and the journal always agree (tested).
- The current week is left out because its score still moves.
- When the `discipline_engine` flag is off for the organisation, the criterion stays "not measured" and says why.
- Weeks with no decision have no score (Z6 never makes up a 100), so they are skipped.

**Your own figures** (§1.5 "risk-of-ruin and Kelly fraction calculators from the user's own stats"), computed by
`app/decision_tools/own_stats.py`. These are pure functions, reused by N2-3. The inputs are the same counted closed
PAPER trades the account and ladder use, with net P&L after charges:

- Counts: trades, wins, losses and flat trades.
- Win rate, average win and average loss, and the payoff ratio (average win / average loss).
- Expectancy per trade: net P&L / trades.
- Profit factor: gross wins / gross losses. It is `null` with no loss.
- **Kelly reference**: the fraction of capital lost on a losing trade that maximises the long-run growth of the past
  outcomes, f = (p·b − q) / (b·(p + q)). Here p is the win share, q the loss share and b the payoff ratio; a flat trade
  counts in neither p nor q. The figure is shown with its formula. It is never used for sizing, which stays with the
  P1-d risk cap. Below zero, it says the past outcomes had a negative expectancy.
- **Risk of ruin (upper bound)**: the chance of ever falling `PAPER_EVIDENCE_RUIN_PCT` % (default 50) below the
  starting capital, if trades kept coming from the user's own past P&L distribution, in rupees.
  - It uses Lundberg's bound ψ ≤ e^(−R·D). R > 0 solves mean(e^(−R·x)) = 1 over the past trade P&Ls x, and D is the
    rupee distance to the ruin level.
  - With a non-positive expectancy, ruin is certain in this model (1.0). With no losing trade, there is nothing to
    bound (null, with a note).
  - For ±1 outcomes the bound equals the classic gambler's-ruin (q/p)^D (tested).
- Below `PAPER_EVIDENCE_MIN_TRADES` (default 20) closed trades, only the counts show, with "n of 20 closed trades".
  Nothing is extrapolated from a handful of trades.
- Every text passes the Z6 advice filter (`journal.review.advice_in`, tested). There are no "should", "consider" or
  "size" verbs.

**UI**: a "Your figures" block and a discipline-weeks strip in the existing ladder card on the Positions page
(`src/progression/LadderEvidence.tsx`). Strings go through a separate i18next instance
(`src/progression/i18n.ts`, namespace `progression`), so only the Positions chunk pays for it. Vitest covers the
formatting helpers, the rendered card, i18n keys and the compliance scan.

**Not in N2-1**: experience levels and gating (N2-2), editable calculators (N2-3), and a LIVE size curve (LIVE stays
locked in Phase 0).

## N2-2 Experience levels

**Flag** `experience_levels` (off by default). While it is off, nothing is gated and both routes answer 404 (tested).

**Level** (`app/paper/experience.py`) comes from the user's own PAPER record: the counted closed trades, the IST
sessions and the largest drawdown, the same inputs as the account and the ladder. It cannot be typed in.

| Level | Reached at | Opens |
|---|---|---|
| Beginner | start | cash underlyings, bought options, hedged structures |
| Intermediate | the PAPER ladder's sessions, trades and drawdown criteria | futures |
| Advanced | `EXPERIENCE_ADVANCED_MIN_TRADES` (100) closed trades over `EXPERIENCE_ADVANCED_MIN_SESSIONS` (60) sessions, drawdown within the ladder's limit | naked short options |

A **naked short option** is a written single-leg option, or a structure whose sold lots of one right outnumber its
bought lots of that right. A short straddle, a short strangle and a ratio spread count; a bull put spread, an iron
condor, a long butterfly and a calendar do not.

**Gate**: `experience.entry_refusals` runs in `execute_signal_for_user`, next to the F&O ban for a resolved contract, and
in `execute_structure`. A raw symbol (TradingView webhook, public API, console, approval, underlying deployments) is
classified by `symbol_gates`:

- the instrument master (FUT / CE / PE, MCX);
- the contract registry (COMMODITY);
- the symbol's shape (`...FUT`, `...25000CE`).

A future or commodity needs the futures gate, and an option sold (SHORT) needs the naked gate (review M1).

Classification steps (review M1 follow-up):

1. **Normalise first.** Upper-case the symbol and strip an `EXCHANGE:` prefix and a `.XX` suffix, keeping both.
2. **Futures:** an NFO / BFO / MCX / CDS venue, TradingView's continuous `NIFTY1!`, a registry COMMODITY root, Upstox
   `X FUT dd MON yy`, `...FUT` or Shoonya `...26F`.
3. **Options:** `parse_contract`, `...25000CE`, Dhan `...-26000-CE` or Shoonya `...26C26000`.
4. **Cash:** the shape `^[A-Z][A-Z0-9&-]*$` (with an optional NSE/BSE prefix or `.NS` / `.BO` suffix), or an index name.
5. **Fail closed:** anything else is an "unrecognised instrument" under the futures gate. A futures opt-out lifts it. Both are new-entry paths only. The P1-d ticket stays cash-only, and the builder basket still
places nothing, so neither needs a gate yet. N2-7 / N2-9 reuse `structure_gates` when a basket becomes placeable.

**Opt-out**: `POST /api/paper/experience/opt-out {gate, reason}`.

- The reason needs at least `EXPERIENCE_OPT_OUT_MIN_REASON_CHARS` (15) characters.
- It covers one gate for the rest of the IST day. It is stored in `experience_opt_outs`, with one row per user, gate and
  day, so it is never counted twice.
- It is written to the audit log (`experience_opt_out`) and counted on the card: today, the last 7 days and in all.
- If the level already opens the gate, the answer is 409.

**UI**: an "Experience level" card on the Positions page (`src/progression/ExperienceCard.tsx`) shows:

- the level chips and the record;
- the next level's criteria;
- each gate's state;
- the opt-out form, for traders on a shut gate only;
- the counts and recent opt-outs.

## Open questions

These are in `docs/OPEN_QUESTIONS.md` (N2-1..N2-10), each with a provisional default.
