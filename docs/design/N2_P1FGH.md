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
| **N2-3** | §1.5 | **Decision tools I** (`claude/n2-3`): a risk-of-ruin and Kelly calculator with editable inputs, prefilled from N2-1's figures, and a what-if for the user's own open positions grouped by underlying (price / IV / days forward; now, scenario and expiry curves) on the builder's model. | `decision_tools` | none / `POST /api/decision/ruin`, `GET /api/decision/positions`, `POST /api/decision/what-if` (tenancy table) | ~1.6k |
| **N2-4** | §1.5 | **Decision tools II** (`claude/n2-4`): an exposure and correlation view across the user's own open positions (by underlying and sector, risk at stops, beta to the NIFTY 50 and pairs that move together, from the daily lake), plus an expected-move cone (±1 / ±2 sd from historical volatility or a typed IV) as a chart layer. | `decision_tools` | none / `GET /api/decision/exposure`, `GET /api/decision/expected-move` | ~1.3k |
| **N2-5** | §1.5 | **Market context panel** (`claude/n2-5`): one tile each for regime (Dow structure of the NIFTY 50, daily and weekly, from the lake), breadth (advances / declines across NSE cash shares on the last two lake days), India VIX (close, change, 1-year rank and percentile, plus the VIX gate's live reading under its own viewer rule), FII / DII (the existing seam, NA until a source with clear terms is configured), global cues (the shared free cache) and events today / this week. Each tile shows its source, as-of time and stale state, and says NA on its own failure. | `context_panel` | none / `GET /api/context/panel` | ~1.1k |
| **N2-6** | §2.4 | **Positions board** (`claude/n2-6`): the user's own open positions with P&L in ₹ and R, stop and target status, time in trade, plan vs current stop (a widened stop is highlighted) and how each exits. Exit now stays one click, with no dialog, during a lockout (tested). Greeks stay on the page's existing table; the exit ladder (partial exits) is deferred (OPEN_QUESTIONS N2-21). | `positions_board` | none / `GET /api/positions/board` | ~1.0k |
| **N2-7** | §3.2 | **Builder adjustments** (`claude/n2-7`): roll a leg (up / down / out), add a hedge, convert a short strangle to an iron condor, close one side. Each shows the changes at model prices, the P&L locked in, the premium today, and the before / after payoff and Greeks. It fills the P1-c drawer. Models only: no order is placed, staged or proposed; "use the legs after" only replaces the page's legs. | `builder_adjustments` | none / `POST /api/options-builder/adjust` | ~1.2k |
| **N2-8** | §3.2 | **Builder compare** (`claude/n2-8`): up to three strategies side by side (net premium, max profit / loss, reward : risk, PoP, margin estimate, theta per day, breakevens, expected move) and their expiry curves on one scale. It covers the page's current legs and saved strategies when `options_builder_saved` is on, each evaluated by the engine at the same spot, date and range. | `builder_compare` | none / none (client-side over `/evaluate`) | ~0.6k |
| **N2-9** | §2.1, §2.5 | **Trade workspace** (`claude/n2-9`): a `/trade` page with the chart on the left, the P1-d PAPER ticket on the right and the positions board below. Keyboard: t ticket, c chart, x exits the focused position, Shift+F flattens all after one confirmation, ? help. On a phone the ticket is a bottom sheet. It shows market, lockout and stale-data states, and keeps the layout per viewer (localStorage, try/catch). It uses existing routes only. | `trade_workspace` | none / none | ~0.8k |
| **N2-10** | §1.6 | **Education pages** (`claude/n2-10`): the method library at `/learn` (trend -> level -> pullback -> commitment -> reward to risk). Each step is linked to where ATP draws it (price-action layer, market zones, evidence charts, the ticket), the screener setups that look for it, the P1-d checklist item that records it and the Z6 tags that show it skipped. It has a 21-term glossary and official NSE / SEBI / CBDT links only. Static English content through i18n, with an advice / claim check. | `education_pages` | none / none | ~0.7k |
| **N2-11** | §1.6, §1.3 | **Learn from your journal** (`claude/n2-11`): cards on the Journal page. Each takes a Z6 behaviour tag on the user's own losing trades (last 8 IST weeks; counted as Z6 counts) with the tagged trades, the losing ones and their net P&L, and links the method-library step about it (`/learn#learn-<step>`). The weekly review itself shipped in Z6-b (#172). | `journal_lessons` | none / `GET /api/journal/lessons` | ~0.5k |

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

## N2-3 Decision tools I

**Flag** `decision_tools` (off by default). While it is off, every `/api/decision/*` route answers 404 (tested).

**Calculator** (`app/decision_tools/calculators.py`, `POST /api/decision/ruin`). Inputs are win rate p, payoff ratio b,
risk per trade r (% of capital) and the ruin level D (% of capital). Each trade is +b·r with chance p, or −r.

- Expectancy per trade is p·b − q, in R.
- The Kelly reference is p − q / b.
- Growth per trade at r, compounding, is p·ln(1 + b·r) + q·ln(1 − r).
- Risk of ruin is Lundberg's e^(−R·D), with p·e^(−R·b·r) + q·e^(R·r) = 1. It is certain with a non-positive expectancy.
  It equals (q/p)^(D/r) for b = 1 (tested).
- A row is given per risk level (0.5 / 1 / 2 / 5 % plus the input), so the effect of the risk per trade can be read off.
- The solver is shared with N2-1 as `own_stats.weighted_adjustment_coefficient`.
- The page prefills p and b from the ladder's `evidence` when it is there.

**What-if** (`app/decision_tools/whatif.py`):

- `GET /api/decision/positions` lists the caller's own open trades grouped by underlying (cash, futures and options
  together).
- `POST /api/decision/what-if` models one group:
  - inputs: the spot, an IV (or one per trade), days forward, an IV shift and the price range;
  - curves: now (T+0), the scenario and the nearest expiry's close (later legs by Black-Scholes), from
    `options_builder.model.pnl_on`;
  - also the P&L at the spot and the scenario's net Greeks.
- Strikes and rights come from the instrument master. A weekly compact symbol runs the day into the strike, so it is
  never parsed. An unreadable leg is listed as left out.
- Spot and IV are typed. Nothing is quoted, so the BT1b entitlement guard is not involved (OPEN_QUESTIONS N2-11).

**UI**: a "Decision tools" card on the Positions page (`src/progression/DecisionToolsCard.tsx`) holds the calculator
table and the what-if SVG chart (now, scenario, expiry), with a story.

## N2-4 Decision tools II

Same flag (`decision_tools`). Both routes answer 404 while it is off (tested). Information only.

**Exposure** (`app/decision_tools/exposure.py`, `GET /api/decision/exposure?days=90`) covers the caller's own open
positions, grouped by underlying as in the what-if. For each underlying it gives:

- the legs and modes;
- the cash and futures notional at entry, and the net option premium;
- the risk at the stops, with the legs that have no stop listed;
- the NSE sector (`classifications`, through `securities`); indices read "Index";
- the beta to the NIFTY 50.

It also gives sector totals and every pair's Pearson r over the aligned daily log returns, needing at least 20 common
days. Pairs at |r| ≥ 0.7 are listed as moving together.

Review fix (DoS): the lake reads and the pair maths cover at most `MAX_COMPUTED` (40) underlyings, those with the
largest risk at stops. The rest are listed as "not computed", and the maths runs in the thread pool.

Daily closes come from the shared lake (`app/decision_tools/market.py`):

- a share with a known ISIN reads the split-adjusted company history (`by_isin.isin_candles`);
- anything else reads the raw `NSE:<SYMBOL>` bars;
- an index reads `NSE_INDEX:<code>`.

This is reference data, so no broker session or BT1b check is involved.

**Expected-move cone** (`GET /api/decision/expected-move?instrument=NSE:RELIANCE&horizon=5`):

- It works from the last daily close and σ, either HV20 (the annualised sample standard deviation of 20 daily log
  returns) or a typed IV.
- It returns rows for 1 / 5 / 10 / 20 sessions and the chosen horizon, with the 1-sd move and the ±1 / ±2 sd prices
  (close × e^(±k·σ·√(h/252))).
- It answers 404 with no closes, and 422 with too few to compute the HV.

The chart window shows an "Expected move" toggle when the organisation has the flag
(`src/progression/ExpectedMoveLayer.tsx`). It draws two bands and the close line through `ChartEngine.setLayer`, beside
the market-zones layer.

**UI**: the Decision tools card gains "Exposure and correlation", loaded on demand
(`src/progression/ExposureSection.tsx`).

## N2-5 Market context panel

**Flag** `context_panel` (off by default; 404 while off, tested). The service is `app/context_panel/service.py` and the
route is `GET /api/context/panel` ("member").

Every tile is built on its own and carries `status` (ok / stale / na), `source` and `as_of`. A tile that raises
becomes "na" with the error's kind; the panel never fails as a whole (tested). Daily lake data is "stale" when more
than one NSE session is missing (holidays from the calendar).

| Tile | Read | Data |
|---|---|---|
| regime | Dow structure: UPTREND after HH + HL, DOWNTREND after LH + LL, else RANGE (`price_action.market_structure`), daily (3-bar fractals) and weekly (W-FRI, 2-bar) | NSE_INDEX:NIFTY50 daily lake bars |
| breadth | advances, declines and unchanged across `NSE:*` cash closes on the last two lake days (dated by the index's own bars) | daily lake (bhavcopy) |
| vix | last close, day change, rank = (last − low) / (high − low) and percentile over 252 sessions; the gate's live value only when `vix_gate.status` reveals it to this viewer (BT1b) | NSE_INDEX:INDIAVIX daily lake; the VIX gate |
| fii_dii | NA until `ai.sentiment.fii_dii_provider` has a configured source (no source with clear terms today) | seam |
| global | each market's last value and change from `ai.global_cues.fetch_all` (shared cache, free public sources, delayed) | public |
| events | `market_events` today and the rest of this IST week, global and the caller's organisation (another organisation's never) | calendar |

No broker session of anyone is read. The UI is a "Market context" card on the dashboard
(`src/progression/ContextPanelCard.tsx`), with tiles in a grid, a stale mark, the NA reason and a Refresh button.

## N2-6 Positions board

**Flag** `positions_board` (off by default; 404 while off, tested). The service is `app/positions_board/service.py` and
the route is `GET /api/positions/board` ("member"). It is read-only.

- **Scope**: the caller's own open trades (tenant + user), valued at the position monitor's last marks (`mark_price`,
  `mark_time`). A trade without a mark is valued at entry and says so. No quote is read.
- **R**: initial risk = |entry − initial stop| × quantity. The initial stop is `initial_stop_loss`, else the P1-d
  plan's stop, else the current stop; the basis is named.
- **Stop status**: through / near (within 0.25 R) / ok.
- **Target status**: reached / near / ok.
- **Drift**: the current stop against the initial or plan stop is widened, trailed or unchanged. A widened stop gets a
  warn border.
- **Totals**: P&L, R, what the stops would give back from the marks, and the counts of widened, near-stop and unmarked
  positions.
- **Lockout**: the daily loss lockout is shown as a fact ("New entries are locked … Exits still work").
- **Exit**: "one_click" (the existing `POST /api/positions/{id}/exit-now`, with no dialog) for a PAPER position the
  caller holds or owns, "broker_app" for LIVE, else "automatic". A lockout never changes it. A test closes a position
  under a recorded lockout through Exit now.
- **UI**: a "Positions board" card at the top of the Positions page (`src/progression/PositionsBoardCard.tsx`). The
  exit button's text says exactly what happens ("Exit PAPER INFY ×10 now").

## N2-7 Builder adjustments

**Flag** `builder_adjustments` (off by default). `POST /api/options-builder/adjust` answers 404 while it is off, and
503 first while the builder's own kill flag is off. It is classified S_COMPUTE. Pure functions live in
`app/options_builder/adjust.py`.

| Kind | Change |
|---|---|
| roll | close one leg, open it `steps` strikes up / down and/or at a later expiry |
| add_hedge | buy the same option `width_steps` strikes further out of the money for one sold leg |
| iron_condor | for sold calls + puts of one expiry with no bought leg, buy wings `width_steps` beyond the outermost shorts (lots summed per side) |
| close_side | close every CE or PE leg (refused when that would close everything) |

**Prices and the comparison:**
- New legs are priced by the model now, at the given IV or the IV of the leg they come from. Closed legs are valued at
  the model now.
- `realised` is the P&L a close locks in against the entry premium. `cash` is the premium the adjustment pays or
  receives today.
- Before is the legs as entered. After is the legs held plus the new legs, plus `realised`, so both curves are the P&L
  since entry.
- Each side gives the payoff at expiry (or on the nearest expiry for a calendar), max profit / loss, breakevens, PoP of
  the legs held and the net Greeks now, plus the Greek change.
- Bad input is refused with 422 (FUT legs, a roll to nowhere, a hedge on a bought leg, an iron condor from a structure
  that has wings).

**No order path:** the answer says `placeable: false` and why. The drawer's "Use the legs after in the builder" only
replaces the page's legs. To act, the trader goes through the existing PAPER ticket / basket flow (OPEN_QUESTIONS
N2-22).

**UI**: the builder's "Adjustments and compare" drawer (`src/progression/AdjustDrawer.tsx`), shown when the flag is on.
It has the form per kind, the changes, both curves (before dashed, after solid) and the comparison table. Compare up to
three strategies is N2-8.

## N2-8 Builder compare

**Flag** `builder_compare` (off by default). It reaches the page through `/api/system/features`, per organisation
(tested). There is no new route: the comparison runs client-side (`src/progression/CompareSection.tsx`,
`compare.ts`) over the existing `POST /api/options-builder/evaluate`.

- **Slots**: up to three. "Add this strategy" copies the page's current legs; "Add a saved strategy" uses the existing
  saved-strategy routes (when `options_builder_saved` is on). The same legs cannot be added twice.
- **Compare**: evaluates every slot at the same spot, as-of date, range (±10 %) and points (121), so all curves share
  one price grid (tested on the backend).
- **Table** (all numbers from the engine): net premium, max profit, max loss (or "unbounded"; a calendar says it has
  no single expiry payoff), reward : risk, PoP, margin estimate ("broker only" when undefined), theta per day,
  breakevens and expected move.
- **Chart**: the expiry curves on one y-scale (the near-expiry curve for a calendar), one colour per slot with its
  chip.
- Models only: nothing is placed.

## N2-9 Trade workspace

**Flag** `trade_workspace` (off by default), read through `/api/system/features` per organisation (tested). The page
is `/trade` (`src/pages/TradeWorkspacePage.tsx`). It is not in the sidebar; the Positions page links to it only while
the flag is on. While the flag is off the page says it is not turned on. **No new route** (tested): it reuses worker
status, the P1-d ticket, the positions board, broker candles and Exit now.

**Depends on `positions_board` (N2-6).** The positions panel, the x key and Shift+F all read `/api/positions/board`,
which answers 404 while `positions_board` is off: the panel is then empty and flatten all reports the error. Turn
`positions_board` on with (or before) `trade_workspace` for an organisation.

- **Layout:**
  - chart (the user's own broker candles through `useBrokerChart`, symbol and timeframe saved) on the left;
  - the PAPER ticket on the right;
  - the positions board below;
  - each panel can be shown or hidden.
  The layout is kept per viewer in `localStorage` (`atp.tradeWorkspace.v1`). Every read and write is wrapped, so a
  private window or blocked storage falls back to the default.
- **Phone:** a "Ticket" button opens the ticket as a bottom sheet.
- **States:**
  - market open / closed with the next open (worker status);
  - the daily loss lockout, shown as a fact ("Exits still work");
  - stale chart data: more than three bars late while the market is open.
- **Keyboard** (never while typing, never with Ctrl / Cmd / Alt):
  - t: go to the ticket;
  - c: go to the chart symbol;
  - x: on a focused position row, Exit now (one key, the same call as the button). Only in the workspace: the board on
    the Positions page has no row focus and no x key;
  - Shift+F: flatten all, after one confirmation that lists the positions, through Exit now for each position the user
    can close here (LIVE ones close in the broker app). A multi-leg structure is one exit (Exit now on a leg closes its
    whole leg group), so its other legs are not tried again and reported as failures;
  - ?: show or hide the shortcut list.
- **Exits** stay one click or one key, never gated. Entries go through the ticket's own checks.
- **Visual regression:** a Playwright run in dark, light and mobile is left to the e2e job (OPEN_QUESTIONS N2-24).

## N2-10 Education pages

**Flag** `education_pages` (off by default; per organisation, tested). The page is `/learn`
(`src/pages/LearnPage.tsx`). It is linked from Coach & Guide only while the flag is on; with the flag off the page says
it is not turned on. **No new route.**

- **Content**: every string is in the i18n namespace `learn` (`src/progression/locales/learn.en.ts`, on the progression
  i18next instance). English only, like the rest of the dashboard.
  - The five steps each have a summary, three short paragraphs, where ATP draws them, the screens that look for them,
    their checklist item and the journal tags that show them skipped.
  - A 21-term glossary (R multiple, expectancy, drawdown, stop, risk cap, IV, PoP, expected move, Greeks, charges, ...).
  - A plain risk note. It cites SEBI's study "Analysis of Profit and Loss of Individual Traders dealing in Equity F&O
    Segment" (25 January 2023): 9 out of 10 individual traders in equity F&O incurred net losses, and later SEBI studies
    report the same pattern. It links to SEBI's official Reports and Statistics - Research list, and says ATP does not
    tell anyone which security to trade.
- **Wording check** (`src/progression/learn.test.tsx`): every string passes the same advice check as the portfolio risk
  text (N1 F3 `risk_text.has_advice`). That is the English advice words as whole words (recommend, advice, should,
  must, buy, sell, reduce, best, guarantee, target price, ...) and the Marathi advice words as written. A
  performance-claim list is also checked (risk-free, sure-shot, "N % win rate", "never lose", "proven method", ...),
  and there is no Devanagari (English bundle).
- **Links**: only `https` to official NSE, SEBI and income-tax (CBDT) hosts (`learn.OFFICIAL_HOSTS`). The test scans
  every URL written in the education sources and rejects lookalike hosts. External links open with
  `rel="noopener noreferrer"`.
- **Drift check** (`tests/test_n2_education_pages.py`): the library's checklist keys, journal tags and screener setup
  keys exist in the P1-d ticket, the Z6 journal and the screener taxonomy.

## N2-11 Learn from your journal

**Flag** `journal_lessons` (off by default). `GET /api/journal/lessons?weeks=8` ("member") answers 404 while it is off,
then 503 while `discipline_engine` is off, like every journal route (tested). The service is
`app/journal/lessons.py`.

- **Data**: the caller's own journal tags (tenant + user, on their own trades) whose trade entered in the last `weeks`
  IST weeks. Tags count as Z6 counts them: CONFIRMED and trader-added always, SUGGESTED while `count_unconfirmed` is on,
  DISMISSED never. The tags are the journal's as of its last refresh; nothing is detected here.
- **A card per tag with at least one closed losing trade**: the tagged trades, the losing ones, their net P&L together
  and the last date. Cards are sorted costliest first.
- **Lesson**: `LESSON_FOR_TAG` gives the method-library step whose "what the journal tags" list holds the tag. A test
  keeps it in step with `src/progression/learn.ts`.
- **UI**: `src/progression/LessonsCard.tsx` at the top of the Journal page. Its strings are in the `learn` namespace, so
  they pass the same advice / claim check as the library. It states counts and links "Read the lesson: 3. Pullback";
  there is no instruction and no promise.

## Open questions

These are in `docs/OPEN_QUESTIONS.md` (N2-1..N2-27), each with a provisional default.
