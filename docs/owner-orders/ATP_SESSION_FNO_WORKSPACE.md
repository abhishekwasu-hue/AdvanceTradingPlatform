From Abhi — F&O TRADING WORKSPACE: watchlists, the symbol hub, trading from the chain, futures, baskets (English). Owner: this is what brokers already give traders (Kite, Dhan, Sensibull); ATP must be at least as easy. Under ui_v2. Same rules: self-review, tests, English only, no secrets; before / after screenshots (desktop + phone, light + dark).

SAFETY FRAME (applies to everything below)
- Phase 0: every order placed in ATP is a PAPER order (live_trading OFF, phase0_paper_only ON). The real-money path stays behind the existing LIVE gates and compliance; do not change them.
- "Trade in your broker app" (broker_app_links): any single order or basket can be sent pre-filled to the user's own broker app; ATP places nothing there.
- Manual orders are the user's own actions: show a confirm step with the margin and the max loss. The existing risk checks apply (risk_by_default, portfolio caps); exits are never blocked.
- Lot size, tick size and freeze quantity come from the instrument master automatically. The default expiry is the nearest one; on the expiry day itself, the next weekly.

1. SYMBOL HUB
- Every symbol page / chart shows tabs at the top: Chart | Option chain | Futures | Overview. Option chain and Futures appear only when the symbol has F&O.
- The same symbol, expiry and timeframe carry across the tabs.

2. F&O WATCHLISTS (next to the index / sector system lists)
- System lists: "F&O stocks (<count>)", "Index futures", "Stock futures (near month)".
- Users can add any option or futures CONTRACT to their own lists (from the chain, the futures chain, search or the chart).
- A contract row shows: the contract name as traders write it (e.g. "NIFTY 28 OCT 22800 CE"), LTP, change, OI, IV (options), and the expiry countdown.
- Tap a row → the contract's chart; quick Buy / Sell; Add to basket; alert. Expired contracts move to "Expired" automatically (with an option to roll to the same strike in the next expiry).

3. OPTION CHAIN TRADING (the v2 chain page)
- Tap a strike (CE or PE) → an action sheet: Buy / Sell, Add to basket, Chart of this strike, Add to watchlist, Price alert.
- A "P" marker on strikes the user holds, with the P&L and Add / Exit right there.
- A bottom strip: PCR, Max Pain, ATM IV, IV percentile, days to expiry.
- A Greeks toggle (Delta, Gamma, Theta, Vega); swipe to change the expiry; tap ATM to recentre; absolute / % change toggle.

4. FUTURES CHAIN
- All expiries of the symbol: LTP, change, OI, OI change, basis vs spot, and the rollover (near → next).
- Each row: Chart, Buy / Sell, Add to basket, Add to watchlist.

5. BASKET ORDERS
- Add legs from the chain, the futures chain, watchlists, the chart and the Options Builder. The basket badge shows the leg count everywhere.
- The basket panel shows:
 - each leg (side, contract, lots, price, editable);
 - the combined payoff chart (at expiry and today / T+0);
 - the margin required WITH the hedge benefit (from the broker's margin API when available, otherwise ATP's estimate, labelled);
 - max profit, max loss, breakevens, PoP;
 - the net premium and the Greeks of the whole basket.
- One-tap ready strategies from the current chain: Bull put spread, Bear call spread, Iron condor, Straddle, Strangle, Calendar. The legs are prefilled around ATM or the user's chosen strike, using the user's own settings (spread width, lots) where they exist.
- "Execute basket" places all legs (PAPER): hedge legs first, then the short legs, so the margin benefit applies. Partial-fill handling is shown plainly.
- Save / load baskets; "Open in Options Builder"; "Trade in your broker app".

6. ONE ORDER TICKET everywhere (chart, chain, watchlist, positions, basket legs)
- Buy / Sell, quantity in LOTS (the units shown), Market / Limit / SL / SL-M, MIS / NRML, the price helper (LTP, bid / ask).
- The margin required and the max loss before confirming; the existing risk checks with plain-word messages.
- Keyboard shortcuts on desktop (B / S), thumb-friendly on phone.

7. TRADE FROM THE CHART
- Buy / Sell buttons on the chart.
- An open position shows on the chart (chart_trades_layer): entry line, SL and target lines that can be dragged to modify (PAPER), and Exit from the chart.

8. POSITIONS
- Each position: a chart button (contract or underlying), Add / Exit / Adjust (e.g. roll a strike, add a hedge, which opens the basket prefilled).
- Strategy grouping: the legs of one basket shown together, with the combined P&L and payoff.

8b. ATP-ONLY FEATURES (what brokers don't give; built on ATP's reader, AI and the user's own settings; information only, never advice, never an automatic order)
 a. Setup-aware chain: on the chain, mark the reader's seller / buyer areas and the invalidation levels against the strikes (e.g. "seller area 23,040–23,120; strikes beyond it"), so the user sees where a credit spread's short strike sits relative to structure. The strike choice stays the user's settings.
 b. "Trade this setup": on a signal card (E1–E4), one tap builds the basket from the user's own settings (spread type, width, lots), with the invalidation level shown. Then the normal confirm / approval.
 c. A payoff in plain words, next to the chart: one sentence for the outcomes, e.g. "If NIFTY closes above 23,150 at expiry you lose about ₹4,200; below 22,900 you keep about ₹1,800."
 d. A pre-order risk line, informational, never blocking: event risk (results, RBI, budget, expiry day, from the events calendar), strike liquidity (wide bid-ask, low OI), margin shortfall.
 e. Premium capture tracker for short-premium positions: the % of the collected premium captured so far, and an alert near the user's own target setting.
 f. Structure alerts: "the seller area of your bear call is breaking", "price is near your short strike", besides the plain price alerts.
 g. Adjustment comparison: when a position is under pressure, show roll / add hedge / exit side by side (payoff, margin, P&L impact); the user decides.
 h. "Explain this" on a basket, a position and the chain: the Copilot explains it in plain words.
 i. Trade cockpit (desktop): chart + chain + basket + positions on one screen (a layout of the Multi-chart workspace); phone: quick switch between them.
 j. Strategy comparison: two or three baskets side by side (payoff, margin, max loss, PoP).
 k. Auto journal: the chart snapshot at entry and exit, saved with each trade (discipline_engine).
 l. PAPER / LIVE always obvious: a clear "Practice mode (PAPER)" frame on every trading screen while in PAPER.

9. TESTS
- For 8b: each item from fixtures (areas on the chain, setup → basket from settings, the payoff sentence, the risk line triggers, the capture %, the structure alerts, the adjustment comparison), with no automatic order anywhere.
- The hub tabs show / hide by F&O availability.
- Contract rows in watchlists; expiry roll-over.
- The chain action sheet: each action.
- The basket: margin with hedge, payoff, the ready strategies, execute order (hedge first), save / load.
- The order ticket: lot units, the risk messages.
- Chart drag of SL / target (PAPER).
- Every order is PAPER under Phase 0 (assert at the order chokepoint).
- Phone layouts.

ORDER: 3 (chain trading) + 6 (the ticket) first, then 5 (basket), 1 (hub), 2 (watchlists), 4 (futures), 7, 8. After each merge: the one deploy line for Abhi.
