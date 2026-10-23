From Abhi — THE COMPLETE TRADING TERMINAL, MASTER ORDER (production grade), design + coding order (English)

This ONE file replaces the earlier ATP_SESSION_TERMINAL_FEATURES.md and the separate Kotak Neo / Groww message: it holds the terminal features, the advanced options-trader features (from research on thinkorswim, Interactive Brokers, tastytrade, Opstra, Sensibull, StockMock / AlgoTest) and the two new brokers.

OWNER PRINCIPLE (record in DECISIONS, binding for every session): ATP is ONE complete, production-grade trading terminal. There is no "paper-phase feature set" and no "later, for live" feature set. Never defer, shrink or drop a good feature because the account is in PAPER today. Every feature is built fully, end to end, production quality, and works TODAY on the PAPER execution path; the SAME feature, code path and UI work LIVE when Abhi himself switches LIVE on (his decision only — the live_trading flag stays as he sets it; nobody else touches it). Safety rules stay: exits are never blocked (ADR-0004); AI never places, blocks or changes orders (ADR-0006); every order the user's own action or his own configured automation.

Record first: docs/owner-orders (INBOX verbatim, DECISIONS: the principle above + "supersedes any earlier deferral of features to a live phase", README row 20 — one row for this master order). Independent review of this order against the code before building (CLAUDE.md §6); fix the findings. Behind ui_v2. One draft PR per part; tests; before / after screenshots (360 / 390 / 1440, light / dark); reuse what exists (the order ticket, basket, paper broker, broker adapters, ExitRules, the chart core) — extend, never rewrite.

References: a trader-built terminal (Kotak Neo based) shown in a public video — strategy reports, sector heatmap, multi-row trade terminal, grouped positions with "exit all", strategy control bar, profit-booking ladder, premium-decay and MTM overlays, SIP / GTT / ATO, a privacy mask, and a "data hub in the middle" architecture. Take the ideas, never copy the look; ATP keeps its own design system (blue accent, colour by meaning). The advanced features are ideas from leading platforms re-thought for ATP's users (options sellers, price-action traders) — never copied; everything is information and tools, never advice; lots / strikes / exits always from the user's own settings.

PART 1 — DATA HUB IN THE MIDDLE (foundation; do first)
1.1 Per user (per broker connection) ONE session and ONE market-data stream (websocket) owned by a backend hub; every consumer — charts, the option chain, watchlists, scans, the paper / live engines, alerts, Telegram, the Copilot tools — subscribes to the hub, never calls the broker for prices itself. Normalised ticks / candles / chain snapshots across brokers (adapter layer); fan-out to the browser over the existing websocket.
1.2 Benefits to prove with numbers in the PR: broker API calls per user per minute before / after, rate-limit errors, one consistent price everywhere, reconnect behaviour (backoff, resubscribe, a clear "reconnecting" state), token-expiry handling (one renew prompt, everything resumes).
1.3 Rules: the user's own token only (data terms); no cross-user sharing of a user's feed; licensed shared EOD history stays separate.

PART 1B — TWO NEW BROKERS: KOTAK NEO AND GROWW (adapters on the hub)
Follow backend/app/brokers (base.py, catalog.py, field_specs.py, token_lifecycle.py, connect.py, registry.py; the existing adapters as patterns). One PR per broker; orders, positions, holdings, margins, the option chain, live data through the hub, token lifecycle, contract-symbol mapping, rate budgets, smoke tests with recorded fixtures (no secrets in git); the plain-words connect guide on the Broker page with the official links.
- Kotak Neo Trade API: access token + TOTP login; a registered static IP is required; 10 orders / sec; Equity + F&O; products CNC / MIS / NRML / CO / BO / AMO; the official Python SDK / REST; verify the websocket feed and the exact auth flow from the current official docs. Charges: on the Trade Free plan, API orders carry ₹0 brokerage at present (statutory charges still apply; the BO square-off leg is charged; the broker may change it) — model it correctly in charges / P&L and say only what the broker says.
- Groww Trade API: a paid subscription (₹499 / month + tax at present); API key + secret (daily approval) or TOTP; REST + websocket (up to 1,000 subscriptions); orders incl. GTT / OCO; F&O; option chain + Greeks; 3 months of history; verify static-IP rules, rate limits and pricing from the official docs; normal brokerage applies.
- Lawyer's list (A6): for LIVE, many users' orders from one ATP server IP under SEBI's static-IP / algo-provider empanelment rules — what each broker requires.

PART 2 — PERFORMANCE PAGE (reports that teach)
- Filters: index / strategy / mode (paper / live), range (Today / This week / This month / YTD / custom), CSV export.
- Headline cards: gross P&L, charges, net P&L; profit days / loss days; trades.
- Streaks: current, best win run, worst loss run; best day and worst day (amounts).
- Metrics: win rate, profit factor, expectancy per trade and per day, average win / average loss, max drawdown, Sharpe (with a plain ⓘ each).
- Charts: daily P&L bars; equity curve with drawdown; EXIT-REASON distribution (SL / TSL / target / time / next level / manual / template exit / ladder) as a donut with counts and P&L per reason; calendar heatmap (green / red days, tooltip with the day's P&L and trades); P&L by model / strategy; P&L by day of week as information only (no rule is ever derived from it).
- Empty states in plain words ("Not enough trades yet — results appear after your first closed trades"), never rows of 0.00.

PART 3 — SECTOR HEATMAP (Markets page)
- Tiles grouped by sector for the selected index (tabs: Nifty 50, Next 50, Nifty 100, Nifty 500, Bank, IT, FMCG, Auto, Metal, Pharma … from the system watchlists), colour by % change (diverging up / down scale from the tokens, colour-blind safe), size optional by weight; tap a tile → the symbol hub (chart / chain).
- A left index panel (all major indices with LTP and % change), sort options; prices through the data hub (batched).

PART 4 — POSITIONS, BUILT FOR OPTIONS SELLERS
- Group by underlying → expiry → legs (collapsible), each group with its net P&L, net Greeks and margin; filters (product, strategy, open / closed); "Open again" on a closed leg.
- Actions: Exit (leg / group), Exit all (one confirm), Add to basket, Modify stop / target (the approved FNO-4-10 path), Reduce lots.
- Privacy mask: one tap hides every ₹ amount (₹••••) across the app; remembered per device.
- Intraday overlay chart: portfolio MTM vs the underlying spot on one chart, with drawdown; premium chart for the position: CE premium, PE premium and combined (premium decay) over time.

PART 5 — STRATEGY / BOT CONTROL BAR (every running strategy or paper / live bot)
- Status page with: Force start, Force stop / close, Freeze (no new entries; exits keep working), Reduce (lots), Exit all for that strategy; live MTM, the legs, the exits that are active (in words + ₹).
- Profit-booking ladder as a user exit setting (ExitRules): tiers "at ₹X per lot profit → book N lots", optional trail after a tier; shown on the status page; backtest + paper + live identical (parity tests).
- Tabs: Status · Reports (Part 2 for this strategy) · Trade log · Config.

PART 6 — TRADE TERMINAL (fast multi-order entry, a pro tool in the Trade menu; beginners keep the simple ticket)
- A grid of rows (exchange, symbol search, LTP live, qty / lots, side, order type, price, entry / exit buttons per row, status, P&L), "clear all", save / load a grid as a template, keyboard shortcuts; every row goes through the same order path, risk checks and confirmations as the ticket; works on paper now and live later unchanged.

PART 7 — ORDER TYPES AND AUTOMATION (full broker-grade set)
- GTT (good till triggered), OCO / bracket-style exits, ATO (at-the-open orders), basket orders (exists — extend), iceberg / slicing for large F&O quantities (exchange freeze limits), and SIP plans (stocks / ETFs: amount or quantity, frequency, day, start / end, max instalments, target corpus, price cap / floor, retries, Telegram notice).
- SMART EXECUTION (Indian market, mandatory for every multi-leg order — ticket, basket, bots, the trade terminal):
  - leg sequencing: BUY (hedge) legs first, then SELL legs, so the margin benefit applies and the order is not rejected; on exit the reverse (close SELL legs first, then BUY); if a later leg fails, a clear state and one-tap "complete / undo" — never a silent naked leg;
  - automatic slicing by the exchange freeze quantity for large F&O orders;
  - pre-order margin with the hedge benefit (the broker's margin API where available) and the free balance after the order;
  - physical-settlement warning for stock F&O: ITM / near-ITM positions approaching expiry flagged in advance (Telegram + Positions), with the delivery value in ₹.
- Each with its own status list, edit / cancel, the order trail and risk checks; executed by the paper broker today and by the broker adapter when LIVE is on (where the broker supports a native GTT / basket, use it; otherwise the platform's own trigger engine with the same semantics, tested).

PART 9 — OPTIONS RISK "WHAT-IF" AND ANALYTICS (information and tools only)
9.1 Risk profile on every payoff (builder, basket, position): sliders for days forward and IV change, price slices, break-even lines, the 1σ expected-move band from the current IV, P&L at a chosen price / date; compare adjustments side by side.
9.2 Portfolio stress test: a grid of spot moves (e.g. ±1 / ±2 / ±3 %, user-editable) × IV change × days forward → P&L per position and in total; aggregated Greeks (delta, gamma, theta, vega) per underlying and portfolio; "what-if I add this trade" before placing (risk and margin change).
9.3 Volatility: IV Rank and IV Percentile (definitions shown), the intraday IV chart, the ATM straddle premium chart, the IV skew chart, India VIX overlay.
9.4 Chain history — START NOW: record option-chain snapshots (LTP, OI, IV, Greeks per strike) for NIFTY / BANKNIFTY / SENSEX (and F&O stocks as capacity allows) at a fixed interval into the data lake, compressed, from the user-licensed / platform-allowed data path only; this builds ATP's own IV / OI history (no paid vendor needed) for 9.3, Part 10 and backtests.
9.5 OI and participants: OI build-up classification per strike over time (long build-up / short build-up / short covering / long unwinding), OI change heat by strike, participant-wise OI (FII / DII / Pro / Client, NSE's daily publication) with trends — also one line in the morning prep.

PART 10 — MARKET REPLAY FOR PRACTICE (ATP's own edge)
- Bar-by-bar replay of a past period on any timeframe (play / pause / step / speed), with paper orders in the replay and their P&L.
- ATP-only: the price-action reader's overlay during the replay — areas, lines, structure and lifecycle events appear as they form (strictly causal, no look-ahead), so a beginner learns to read the chart while practising.
- Options replay from the recorded chain history (9.4) when it exists; a plain note where it does not.

PART 11 — POSITION MANAGEMENT HELPERS (the user decides)
- Roll "what-if": rolling to the next weekly or another strike shows the credit / debit, the new break-evens, the new risk and margin — options and numbers only, never a recommendation; one tap opens it as a basket for the user to review.
- Expiry-day reminders: positions expiring today, the next weekly used, the physical-settlement warning (Part 7).

PART 12 — AI ON TOP (Copilot; never places orders)
- Risk questions in plain language: "What is my risk now?", "What if NIFTY falls 300?" — the Copilot runs the 9.2 stress test with the user's own key and answers in words with the table.
- Post-trade review: every closed trade auto-journaled with its chart, entry, exit and reason; a weekly "patterns in your trades" note (e.g. "5 trades lost after the stop was moved closer") — observations, not advice.

PART 8 — SAFETY AND QUALITY GATES (apply to every part)
- Exits never blocked; Exit all always available; kill switch respected; AI never places orders.
- Every order path: idempotency keys, the order trail, retries with no duplicates, partial fills handled, the same code for paper and live (paper = a broker adapter, not a separate feature).
- Tests: unit + e2e per feature; parity (backtest = paper = live semantics) for exits and the ladder; load test of the data hub (B10).

ORDER (after the current trader-pack / watchlist work)
1. Review. 2. Part 1 (data hub) + Part 9.4 (start recording the chain history immediately — the earlier it starts, the more history we own). 3. Part 7 smart execution (hedge-first sequencing, freeze slicing, margin with hedge benefit, physical-settlement warning). 4. Part 1B (Kotak Neo, then Groww). 5. Part 9.1–9.2 (what-if risk + stress test). 6. Part 2 (Performance) and Part 3 (Sector heatmap) in parallel. 7. Part 4 (Positions). 8. Part 5 (control bar + ladder). 9. Part 9.3 + 9.5 (volatility, OI, participants). 10. The rest of Part 7 (GTT / OCO / ATO / iceberg / SIP). 11. Part 10 (replay with the reader overlay). 12. Parts 11, 12, 6.
Report to Abhi in Marathi after each part with screenshots.
