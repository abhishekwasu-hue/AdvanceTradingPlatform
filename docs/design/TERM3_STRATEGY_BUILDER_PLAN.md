# TERM-3 - Trade's options Strategy Builder as a Terminal tab (plan, waits for the owner's approval)

Owner 30 Oct: "Trade already has an options strategy builder (set up strategies, place orders from there); copy it into
ATP, a tab in the Terminal; check first, then plan."

## What Trade has (checked, Trade main 36af8ce)
- UI: `page_dashboard.py` `_render_strategy_builder()` (lines 73-427, Streamlit fragment): ready-made strategies (one
  click), add / remove legs (BUY/SELL, CE/PE, strike, lots), shift all strikes, payoff diagram, combined Greeks,
  Execute (PAPER / LIVE with a confirm). Manual panel + basket with basket SL / target: lines 590-800.
- Logic: `strategy_payoff.py` (151 lines): `compute_leg_payoff`, `compute_strategy_payoff_curve`,
  `READY_MADE_CATEGORIES`, `build_ready_made_strategy(name, atm, hedge_width)`.
- Orders: `trading_engine.open_multi_leg_trade` / `execute_order_leg_set` straight to Upstox with Trade's own token.

## Why not a file copy
Streamlit (Python UI) vs ATP's React page; and CLAUDE.md §4: ATP uses its OWN Upstox app, never the Trade bot's -
Trade's order code cannot run in ATP. ATP already has the pieces the order path needs (TERM-2): the one chain
(ChainLadderV2), the one basket (BasketPanelV2: legs, payoff, max P/L, breakevens, POP, margin, one Place - PAPER,
exits never blocked) and the Options Builder page (templates, legs).

## Plan (port the logic, reuse ATP's order path)
1. Terminal tab "Strategy builder" (desktop preset + phone tab) = BasketPanelV2 + the chain, one symbol + expiry.
2. Port from Trade, as pure functions with tests (same numbers as Trade on the same legs):
   ready-made list + `build_ready_made_strategy` (backend options_builder), "shift all strikes", combined Greeks.
3. Orders: ATP's existing basket Place (PAPER; same path LIVE, switches are the owner's). No Trade order code.
4. Rule §3: lots, width, SL / target only from the user's own settings - blank = not chosen (Trade's
   `hedge_width` default and basket SL 1000 are NOT copied).
5. Behind a flag `term_strategy_builder` (OFF); before / after desktop + 375 px on one page; one PR.

## Owner decisions (defaults)
- D1 template width with no user setting: the user picks it before Load (default) / Trade's hedge width.
- D2 basket SL / target: ATP's own exit fields, blank = off (default).
- D3 LIVE: PAPER only now (default).
