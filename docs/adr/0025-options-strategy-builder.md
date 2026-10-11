# ADR-0025: The Options Strategy Builder - a port of the Trade repo's logic, pure functions, golden parity

**Date:** 2026-10 · **Status:** proposed (P1-a) · **Phase:** P1

## Context
The P1 spec asks for an options strategy builder at the level of Sensibull or Opstra. The Trade repo already has the
logic, in use and tested:
- payoff at expiry, combined Greeks, breakevens and max profit / loss;
- 13 ready-made templates, with the hedge-first margin rule;
- a per-lot strategy result with an equal-lots rule and a positive max loss;
- PoP-driven and rule-based strike selectors;
- position sizing.

Rewriting that logic risks drifting from behaviour the owner trades with. The platform's own rules also apply:
- no hardcoded instruments;
- tests for everything;
- exits are never blocked;
- the AI acts only on approval.

## Decision
1. **Port, do not rewrite.**
   - `backend/app/options_builder/` holds the Trade functions as pure, typed functions with English docstrings.
   - Behaviour stays bit-for-bit. A golden set is produced by the Trade functions themselves
     (`tests/fixtures/options_builder/make_golden.py`, Trade@73f652c, 1,451 cases over four synthetic chains), and
     the ported functions must reproduce it.
   - The Trade repo's own unit tests are ported as they are.
2. **Instrument numbers are parameters.**
   - The strike step, hedge width and ITM depth have no defaults: the Trade defaults were NIFTY's.
   - The caller takes them, and the lot size, from the instrument master.
   - A test checks that none of these defaults come back.
3. **Greeks come from the platform's model where possible.** `leg_with_model_greeks` uses
   `app/option_chain/greeks.py`: the IV is the leg's own or solved from its premium, never invented. The ported
   netting rule agrees with `compute_strategy_greeks` (a test).
4. **Hedge first everywhere.** Templates list each BUY before the SELL it protects, and `hedge_first()` orders any
   basket that way before it reaches the order ticket. The equal-lots rule and the positive max-loss convention are
   kept.
5. **The builder never places an order.** It computes. Placing a strategy goes through the same execution and risk
   layers as every order, as a basket in hedge-first order (P1-c / P1-d).

## Consequences
- Later extensions build on this core and must keep the golden set passing:
  - T+0 and IV / time scenarios, more templates, adjustments and compare (P1-b, P1-g);
  - the UI (P1-c).
- Any intended change of behaviour regenerates the golden set from a new Trade commit, or is recorded here as a
  deliberate departure.
- The selectors read the broker's raw chain shape (Upstox: `strike_price`, `call_options.market_data.ltp`,
  `option_greeks.pop`). An adapter from the platform's chain model comes with P1-b.
