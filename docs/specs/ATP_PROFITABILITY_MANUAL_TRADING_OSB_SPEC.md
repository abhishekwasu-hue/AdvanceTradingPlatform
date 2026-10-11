From Abhi — ATP spec P1: the profitability mission, manual-trading UI, and the Options Strategy Builder ported from the Trade repo (English). Same working rules as MASTER SPEC (never stop to wait; OPEN_QUESTIONS with provisional defaults; multitask; self-review every diff; WORK_LOG). Draft PRs only; no LIVE; tests for everything; English-only strings; no hardcoded symbols/dates/prices. ADR-0004 (exits never blocked) and ADR-0006 (AI acts only on human approval) unchanged.

══════════════════════════════════
0. Mission statement (goes into docs/MISSION.md and the product copy)
══════════════════════════════════
ATP exists to help loss-making retail traders become consistently disciplined and, through that, give them a real chance at profitability. No guarantee is made or implied anywhere in the product (regulator studies show most retail F&O traders lose; the product copy states this plainly). Every feature is judged by one question: does it reduce the behaviours that lose money (oversizing, no stop, revenge trading, overtrading, trading without a plan, ignoring costs and events) or increase the behaviours that make money (edge-based setups, risk control, review, patience)? Compliance wording: ATP is a tool and education platform; it never recommends securities.

══════════════════════════════════
1. Profitability feature set (what the platform must have; each item: where it lives, tests)
══════════════════════════════════
1.1 Risk by default (risk_engine, settings, order ticket)
 - Mandatory per-trade risk cap (% of capital or ₹) and position sizing from the stop distance or max loss of the spread; the ticket computes quantity, the user cannot exceed the cap without a deliberate override that is logged and counted.
 - Daily loss limit with lockout (no new entries for the day; exits allowed), weekly limit, max trades per day, max open positions, max exposure per underlying, consecutive-loss cooling-off (timer), expiry-day and event-day guardrails (results, policy days, budget — from the events calendar), naked-short restrictions by plan/experience level, overnight-naked warning.
 - Drawdown guard: size reduces automatically as drawdown grows (configurable curve); recovery ladder back to normal size.
 - Cost awareness: every ticket and every backtest shows brokerage, STT, exchange charges, GST, stamp duty, slippage estimate and break-even move; a "cost of overtrading" meter in the journal.
1.2 Plan before trade (order ticket flow)
 - A trade plan is required for discretionary entries: setup name (from the method library: pullback at level, S/R flip, breakout retest, range edge, OI/IV based, other), direction, entry, stop, target, R:R (must meet the user's minimum, default from settings), time stop, invalidation note. The ticket is built from the plan; the plan is stored with the trade and compared with what actually happened.
 - Pre-trade checklist (user-editable, defaults from the method): trend aligned on higher TF, level identified, pullback confirmed, commitment candle, R:R ≥ min, no event within the hold window, liquidity ok, size within cap. Checklist result is stored; skipping is possible but counted as a discipline miss.
1.3 Journal and analytics (the heart of the product)
 - Auto-journal every trade (manual, scan, strategy): plan vs execution, screenshots at entry/exit (chart snapshots), fills, costs, MAE/MFE, holding time, Greeks at entry for options, IV at entry/exit.
 - Analytics: expectancy, win rate, average R, profit factor, drawdown, by setup / instrument / time of day / day of week / DTE / IV regime / trend regime / after-loss vs after-win, equity curve with R-multiples, distribution of outcomes, streaks.
 - Behaviour tags (automatic where detectable, user-confirmable): revenge trade (entry within N minutes after a loss, larger size), FOMO entry (chasing beyond plan entry), averaging a loser, moved stop against position, no stop, overtrading (trades above the daily norm), early exit before target with no invalidation, held through event. A discipline score (0–100) per week with the components shown.
 - Weekly review: generated report (numbers from the data; Copilot explains in plain language, no advice wording): what worked, what lost, which behaviours cost money, one habit to fix next week; comparison with the user's own previous weeks only (no leaderboard by default).
1.4 Progression ladder
 - Paper first: new users and new strategies/scans start in PAPER; graduation criteria to LIVE (minimum sessions, trade count, discipline score, drawdown within policy) visible as a progress card; LIVE size starts small and scales with evidence (user-configurable curve).
 - Experience levels gate complexity (naked options, high leverage) with an explicit, logged opt-out.
1.5 Decision tools (information, never advice)
 - Probability and scenario tools: PoP from the chain, expected move, probability cone on chart, what-if on IV/time/price for any position (T+0 lines), risk-of-ruin and Kelly fraction calculators from the user's own stats, correlation/exposure view across positions.
 - Market context panel: regime (trend/range by Dow on W/D), breadth, VIX/IV rank, FII/DII flows, global cues, events today/this week — the context Abhi's method requires before timing an entry.
1.6 Education inside the product
 - Method library pages (trend → level → pullback → commitment → R:R), each concept linked to the chart layer that draws it and to the screens that find it; glossary; "learn from your journal" cards that link a losing pattern to the relevant lesson.
1.7 Tests: sizing math, lockouts (exits still pass), cooling-off timers, cost calculations vs exchange schedules (config-driven), plan/ticket binding, behaviour-tag detectors on fixture journals, discipline score determinism, graduation criteria, weekly report numbers vs journal.

══════════════════════════════════
2. Manual trading UI (attractive, fast, safe) — sits beside auto trading, same execution and risk layers
══════════════════════════════════
2.1 Workspace "Trade": chart (CHARTING_SPEC engine) on the left, option chain / order ticket on the right, positions and orders strip at the bottom; layouts saved; keyboard shortcuts (buy/sell ticket, close position, flatten all with confirmation); mobile layout with a bottom sheet ticket.
2.2 Order ticket: instrument search (underlying + expiry + strike + CE/PE), product/validity, limit price with ladder (±tick buttons), quantity in lots with the sizing helper (risk cap → lots), estimated margin (broker seam), estimated cost and break-even, SL/target fields that draw on the chart as draggable lines, bracket/cover options, basket for multi-leg (hedge legs placed first — the margin rule from the Trade repo), preview with the full plan, one confirm. No MARKET orders when the SEBI retail-algo policy applies to the tenant (limit with protection instead).
2.3 Option chain: ATM-centred, OI/volume/IV/Greeks columns, OI bars inline, max-pain and OI-wall markers, PCR and banner (OI_BANNER_SPEC) on top, click a strike to add a leg to the ticket or to the Strategy Builder, expiry tabs with DTE, straddle/strangle premium row, IV skew sparkline.
2.4 Positions: live P&L (₹ and R), Greeks per position and portfolio, SL/target status, time in trade, plan vs current (drifted stop highlighted), one-click adjustments (roll, add hedge, convert to spread), exit ladder; exits always available even during lockout.
2.5 Design: follows U5 tokens; the memorable element here is the ticket's "risk gauge" — a single arc that fills as quantity approaches the risk cap and turns to the warn colour beyond it; everything else quiet. Confirm button text says exactly what happens ("Place 2 lots bear call spread"). States: pre-market, closed market, lockout (explains why and until when), stale data.
2.6 Tests: ticket math (price, quantity, margin, costs), hedge-first ordering, lockout behaviour, drag-SL sync with the order, basket atomicity and partial-fill handling, keyboard flows, visual regression dark/light/mobile.

══════════════════════════════════
3. Options Strategy Builder — port from the Trade repo and raise to Sensibull/Opstra level
══════════════════════════════════
3.1 Source (Trade repo): strategy_payoff.py (compute_leg_payoff, compute_strategy_payoff_curve, compute_combined_greeks, find_breakeven_points, compute_max_profit_loss, build_ready_made_strategy with 13 templates and the hedge-first margin rule, build_strategy_result_from_legs with the equal-lots rule and positive max_loss convention, READY_MADE_CATEGORIES, build_default_price_range) and strategy.py (PoP-driven selectors: select_iron_condor with combined PoP widening, select_iron_butterfly, select_credit_spread, select_credit_spread_fixed_strikes, select_credit_spread_itm, select_naked_option_itm with optional hedge, compute_position_size). Port the logic to backend/app/options_builder/ as pure, typed functions (English docstrings; per-underlying strike step and lot size from the instrument master; thresholds as parameters; reuse option_chain/greeks.py and leg_greeks.py for Greeks instead of broker-supplied values where possible). Port the Trade tests as golden fixtures (same input → same output).
3.2 Extend to best-in-class
 - Payoff at expiry AND T+0 / any date lines using Black-Scholes with per-leg IV (from the chain), IV shift slider, days-forward slider, underlying slider; breakevens, max profit/loss, PoP (from chain Greeks when present, else model), expected move overlay, probability-weighted P&L.
 - Greeks: per leg and net (delta, gamma, theta, vega, rho), theta decay chart, delta vs price chart, vega exposure by expiry.
 - Margin: broker margin seam (SPAN/exposure) with benefit for hedged legs; estimated when the seam is unavailable, clearly labelled.
 - Templates: 30+ (all bullish/bearish/neutral/volatility families incl. butterflies, condors, ratio spreads, calendars, diagonals, jade lizard, covered call/collar for stock options, synthetic futures); "build from chain" by clicking strikes; "suggest strikes by rule" (ATM±n, delta, premium, PoP target — the ported selectors) with the rule shown.
 - Adjustments: roll leg (up/down/out), add hedge, convert to iron condor, close one side; each adjustment shows the before/after payoff and Greeks.
 - Compare up to three strategies side by side (payoff, PoP, max loss, margin, theta/day, breakevens).
 - Position sizing from the risk cap (compute_position_size ported), cost line (brokerage/STT etc.), break-even move including costs.
 - Save/share (tenant), load into the order ticket as a basket (hedge legs first), deploy to PAPER/LIVE through the same execution and risk layers, attach to a scan's "Then" template (U4), or to a Strategy for automated management.
 - Analytics after entry: the built strategy becomes a position with live payoff and Greeks (links to the positions UI and option-contract charts in CHARTING_SPEC).
3.3 UI design (U5 tokens): the memorable element is the payoff canvas — expiry and T+0 curves on one chart with the underlying price marker, breakevens and max-loss shading, the probability cone behind it and draggable strikes on the x-axis (drag a strike, the curve updates live). Left: leg table (direction, type, strike, expiry, lots, premium, IV, Greeks) with inline editing; right: metrics card (net credit/debit, max profit/loss, PoP, margin, theta/day, breakevens, cost, R:R) and the risk gauge; top: template gallery with live mini payoff thumbnails; bottom: adjustments and compare drawer. Plain copy: "Add leg", "Place as basket", "Save strategy". Mobile: payoff first, legs below.
3.4 Tests: golden parity with Trade functions; Black-Scholes T+0 vs reference values; breakevens for every template; equal-lots and hedge-first rules; margin seam fallback labelled; PoP consistency; drag-strike updates deterministic; basket export matches legs; visual regression.

══════════════════════════════════
4. Build order (one draft PR each; ADR-0025 options builder, ADR-0026 discipline engine)
══════════════════════════════════
P1-a Options builder core port + tests (3.1). P1-b T+0/IV/time model, PoP, templates, selectors UI (3.2 first half). P1-c Builder UI (3.3) + basket/order-ticket integration. P1-d Risk-by-default + plan-before-trade ticket flow (1.1, 1.2, 2.2). P1-e Journal auto-capture + analytics + behaviour tags + discipline score (1.3). P1-f Progression ladder + decision tools + context panel (1.4, 1.5). P1-g Manual trading workspace polish, positions UI, adjustments, compare (2.x, 3.2 second half). P1-h Education pages + weekly review report (1.6, 1.3). Each PR: screenshots, design notes, tests, WORK_LOG.
